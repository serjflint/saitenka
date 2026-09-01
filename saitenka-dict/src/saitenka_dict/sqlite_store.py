from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import weakref
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saitenka_dict.metadata import parse_frequency
from saitenka_dict.models import (
    Definition,
    Frequency,
    KanjiEntry,
    Pronunciation,
    SourceTrace,
    Tag,
)
from saitenka_dict.store import CacheObserver, TermRecord, TermSearch

if TYPE_CHECKING:
    from collections.abc import Iterable

_TERM_QUERY_LEGACY = (
    "SELECT d.id, d.title, d.import_order, e.id, e.term, e.reading, e.glossary, "
    "e.tags, e.seq, '' AS rules, 0 AS score, '' AS term_tags "
    "FROM keys k JOIN entries e ON e.dict_id=k.dict_id AND e.id=k.id "
    "JOIN dictionaries d ON d.id=e.dict_id "
    "LEFT JOIN json_each(?) requested ON requested.value=d.title "
    "WHERE k.key IN (SELECT value FROM json_each(?)) "
    "AND (requested.value IS NOT NULL OR ? = '[]') "
    "GROUP BY d.id, e.id ORDER BY COALESCE(requested.key, d.import_order), score DESC, e.id"
)
_TERM_QUERY_ENRICHED = (
    "SELECT d.id, d.title, d.import_order, e.id, e.term, e.reading, e.glossary, "
    "e.tags, e.seq, e.rules, e.score, e.term_tags "
    "FROM keys k JOIN entries e ON e.dict_id=k.dict_id AND e.id=k.id "
    "JOIN dictionaries d ON d.id=e.dict_id "
    "LEFT JOIN json_each(?) requested ON requested.value=d.title "
    "WHERE k.key IN (SELECT value FROM json_each(?)) "
    "AND (requested.value IS NOT NULL OR ? = '[]') "
    "GROUP BY d.id, e.id ORDER BY COALESCE(requested.key, d.import_order), score DESC, e.id"
)
_RELATED_QUERY_LEGACY = (
    "SELECT d.id, d.title, d.import_order, e.id, e.term, e.reading, e.glossary, "
    "e.tags, e.seq, '' AS rules, 0 AS score, '' AS term_tags "
    "FROM entries e JOIN dictionaries d ON d.id=e.dict_id "
    "LEFT JOIN json_each(?) requested ON requested.value=d.title "
    "WHERE e.seq IN (SELECT value FROM json_each(?)) "
    "AND (requested.value IS NOT NULL OR ? = '[]') "
    "ORDER BY COALESCE(requested.key, d.import_order), score DESC, e.id"
)
_RELATED_QUERY_ENRICHED = (
    "SELECT d.id, d.title, d.import_order, e.id, e.term, e.reading, e.glossary, "
    "e.tags, e.seq, e.rules, e.score, e.term_tags "
    "FROM entries e JOIN dictionaries d ON d.id=e.dict_id "
    "LEFT JOIN json_each(?) requested ON requested.value=d.title "
    "WHERE e.seq IN (SELECT value FROM json_each(?)) "
    "AND (requested.value IS NOT NULL OR ? = '[]') "
    "ORDER BY COALESCE(requested.key, d.import_order), score DESC, e.id"
)
_SEARCH_QUERY_LEGACY = (
    "SELECT d.id, d.title, d.import_order, e.id, e.term, e.reading, e.glossary, "
    "e.tags, e.seq, '' AS rules, 0 AS score, '' AS term_tags "
    "FROM entries e JOIN dictionaries d ON d.id=e.dict_id "
    "LEFT JOIN json_each(?) requested ON requested.value=d.title "
    "WHERE (e.term GLOB ? OR e.reading GLOB ?) "
    "AND (requested.value IS NOT NULL OR ? = '[]') "
    "ORDER BY COALESCE(requested.key, d.import_order), score DESC, e.id LIMIT ?"
)
_SEARCH_QUERY_ENRICHED = (
    "SELECT d.id, d.title, d.import_order, e.id, e.term, e.reading, e.glossary, "
    "e.tags, e.seq, e.rules, e.score, e.term_tags "
    "FROM entries e JOIN dictionaries d ON d.id=e.dict_id "
    "LEFT JOIN json_each(?) requested ON requested.value=d.title "
    "WHERE (e.term GLOB ? OR e.reading GLOB ?) "
    "AND (requested.value IS NOT NULL OR ? = '[]') "
    "ORDER BY COALESCE(requested.key, d.import_order), score DESC, e.id LIMIT ?"
)
_EXACT_TERMS_QUERY = (
    "SELECT requested.value FROM json_each(?) requested "
    "WHERE EXISTS ("
    "SELECT 1 FROM dictionaries d "
    "JOIN keys k ON k.dict_id=d.id AND k.key=requested.value "
    "JOIN entries e ON e.dict_id=k.dict_id AND e.id=k.id "
    "WHERE e.term=requested.value "
    "AND (? = '[]' OR d.title IN (SELECT value FROM json_each(?)))"
    ")"
)
_MEDIA_QUERY = (
    "SELECT m.path, m.png FROM media m JOIN dictionaries d ON d.id=m.dict_id "
    "WHERE d.title=? AND m.path IN (SELECT value FROM json_each(?))"
)


def meta_lookup_terms(headwords: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Every string a ``term_meta`` row for these headwords may be keyed under.

    Both the terms and their readings, because a dictionary may key an entry either way. Selecting
    terms alone made a kana-keyed row unreachable for a word written in kanji: for 本命 the term is
    本命 and never ほんめい, so a pitch or frequency dictionary holding only the kana row contributed
    nothing. In NHK 2016 that is 13,941 rows, covering 12,228 kanji-written headwords that had a
    pitch pattern the product never showed.

    Widening what is *selected* is only half of it — see :func:`prefer_term_keyed` for the half that
    keeps the extra rows from attaching one accent to every homophone.
    """
    return tuple(
        dict.fromkeys(part for term, reading in headwords for part in (term, reading) if part)
    )


def prefer_term_keyed(rows: Iterable[Any], headwords: tuple[tuple[str, str], ...]) -> list[Any]:
    """Drop a reading-keyed row when the same dictionary also answered under the headword's own term.

    A row keyed by a kana reading is inherently ambiguous: ほんめい identifies a reading, not a word,
    and the data carries nothing further to disambiguate it. Admitting those rows unconditionally
    would attach one accent to every homophone — of the 12,228 headwords this recovers, only 2,024
    have a reading that maps to a single kanji headword.

    So precision wins where a dictionary has it: per (dictionary, reading), a term-keyed answer
    suppresses that dictionary's kana-keyed one. A dictionary that only has ほんめい still
    contributes, and one that has 本命 is not second-guessed. The choice is per dictionary, so a
    precise dictionary does not silence a vaguer one that is the only source for some other word.

    Rows are ``(title, term, reading, *rest)`` in query order, which is preserved.
    """
    pairs = set(headwords)
    reading_of_term = {term: reading for term, reading in headwords if term}
    readings = {reading for _term, reading in headwords if reading}
    materialised = list(rows)

    def exact(term: str, reading: str | None) -> bool:
        # A row with no reading is identified by its term alone — that is all it claims.
        return (term, reading) in pairs or (reading is None and term in reading_of_term)

    precise = {
        (row[0], row[2] if row[2] is not None else reading_of_term.get(row[1]))
        for row in materialised
        if exact(row[1], row[2])
    }

    def keyed_by_our_reading(title: str, term: str, reading: str | None) -> bool:
        return (
            term in readings
            and (reading is None or reading == term)
            and (title, term) not in precise
        )

    return [
        row
        for row in materialised
        if exact(row[1], row[2]) or keyed_by_our_reading(row[0], row[1], row[2])
    ]


_FREQUENCY_QUERY = (
    "SELECT d.title, m.term, m.reading, m.rank, m.disp FROM term_meta m "
    "JOIN dictionaries d ON d.id=m.dict_id "
    "WHERE m.mode='freq' AND m.term IN (SELECT value FROM json_each(?)) "
    "AND (? = '[]' OR d.title IN (SELECT value FROM json_each(?))) "
    "ORDER BY d.import_order, m.rowid"
)
_PRONUNCIATION_QUERY = (
    "SELECT d.title, m.term, m.reading, m.mode, m.positions FROM term_meta m "
    "JOIN dictionaries d ON d.id=m.dict_id "
    "WHERE m.mode IN ('pitch', 'ipa') AND m.term IN (SELECT value FROM json_each(?)) "
    "AND (? = '[]' OR d.title IN (SELECT value FROM json_each(?))) "
    "ORDER BY d.import_order, m.rowid"
)
_FREQUENT_TERMS_QUERY = (
    "SELECT m.term, m.reading, MIN(m.rank) FROM term_meta m "
    "JOIN dictionaries d ON d.id=m.dict_id "
    "WHERE m.mode='freq' AND m.rank>0 "
    "AND (? = '[]' OR d.title IN (SELECT value FROM json_each(?))) "
    "GROUP BY m.term ORDER BY 3 LIMIT ?"
)
_KANJI_QUERY = (
    "SELECT d.id, d.title, d.import_order, k.rowid, k.chr, k.onyomi, k.kunyomi, "
    "k.tags, k.meanings, k.stats FROM kanji k JOIN dictionaries d ON d.id=k.dict_id "
    "WHERE k.chr IN (SELECT value FROM json_each(?)) "
    "AND (? = '[]' OR d.title IN (SELECT value FROM json_each(?))) "
    "ORDER BY d.import_order, k.rowid"
)
_KANJI_FREQUENCY_QUERY = (
    "SELECT d.title, m.chr, m.value FROM kanji_meta m "
    "JOIN dictionaries d ON d.id=m.dict_id "
    "WHERE m.mode='freq' AND m.chr IN (SELECT value FROM json_each(?)) "
    "AND (? = '[]' OR d.title IN (SELECT value FROM json_each(?))) "
    "ORDER BY d.import_order, m.rowid"
)


def _close_all(connections: list[sqlite3.Connection]) -> None:
    while connections:
        with contextlib.suppress(sqlite3.Error):
            connections.pop().close()


class SqliteDictionaryStore:
    """Read-only adapter for Saitenka's schema-1 consolidated dictionary database."""

    def __init__(
        self,
        path: str | Path,
        *,
        entry_cache_max: int = 256,
        cache_observer: CacheObserver | None = None,
    ):
        self.path = Path(path)
        self._local = threading.local()
        # `threading.local` can only reach the calling thread's connection, so a store that is simply
        # dropped hands every OTHER thread's to the interpreter, which finalises them on an arbitrary
        # later collection — one `ResourceWarning: unclosed database` each, naming no leak site. The
        # roster gives the finalizer something it can close. (The list holds no reference to `self`.)
        self._connections: list[sqlite3.Connection] = []
        weakref.finalize(self, _close_all, self._connections)
        self._entry_cache_max = max(0, entry_cache_max)
        self._cache_observer = cache_observer
        self._entry_caches: dict[int, OrderedDict[int, TermRecord]] = {}
        self._entry_lock = threading.Lock()
        self._tag_cache: dict[int, dict[str, Tag]] = {}
        self._tag_lock = threading.Lock()
        self._enriched: bool | None = None

    def _conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            uri = f"file:{self.path.resolve()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._local.connection = connection
            # No lock: one open per thread, and list.append is atomic with and without the GIL.
            self._connections.append(connection)
        return connection

    @staticmethod
    def _dictionary_args(dictionaries: tuple[str, ...]) -> tuple[str, str]:
        encoded = json.dumps(dictionaries, ensure_ascii=False)
        return encoded, encoded

    def _has_enriched_entries(self) -> bool:
        """Whether ``entries`` carries the full Yomitan columns.

        No current writer produces the narrow shape — :mod:`saitenka_dict.schema` widens any database
        it opens — but this store opens **read-only**, so a file that has not yet been opened for
        writing can still be the old one. That makes this on-disk *format* compatibility, not a second
        client: it costs one PRAGMA per store rather than one per query, and it retires when the
        narrow shape is provably gone from the wild.
        """
        if self._enriched is None:
            columns = {row[1] for row in self._conn().execute("PRAGMA table_info(entries)")}
            self._enriched = {"rules", "score", "term_tags"} <= columns
        return self._enriched

    def _tags(self, dictionary_id: int) -> dict[str, Tag]:
        with self._tag_lock:
            cached = self._tag_cache.get(dictionary_id)
        if cached is not None:
            return cached
        columns = {row[1] for row in self._conn().execute("PRAGMA table_info(tags)")}
        query = (
            "SELECT code, name, ord, category, notes, score FROM tags WHERE dict_id=?"
            if "score" in columns
            else "SELECT code, name, ord, category, notes, 0 FROM tags WHERE dict_id=?"
        )
        rows = self._conn().execute(query, (dictionary_id,))
        result = {
            code: Tag(
                (name or code).replace("\N{NO-BREAK SPACE}", " "),
                category or "",
                notes or "",
                order or 0,
                tag_score or 0,
            )
            for code, name, order, category, notes, tag_score in rows
        }
        with self._tag_lock:
            return self._tag_cache.setdefault(dictionary_id, result)

    @staticmethod
    def _split_tags(raw: str | None, tags: dict[str, Tag], source: SourceTrace) -> tuple[Tag, ...]:
        result = [
            replace(tags.get(code, Tag(code)), source=source)
            for code in (raw or "").split(" ")
            if code
        ]
        return tuple(sorted(result, key=lambda tag: (tag.order, tag.name)))

    def find_terms(self, search: TermSearch) -> tuple[TermRecord, ...]:
        keys = tuple(dict.fromkeys(form for form in search.forms if form))
        if not keys or search.limit < 1:
            return ()
        query = _TERM_QUERY_ENRICHED if self._has_enriched_entries() else _TERM_QUERY_LEGACY
        rows = (
            self._conn()
            .execute(
                query,
                (
                    json.dumps(search.dictionaries, ensure_ascii=False),
                    json.dumps(keys, ensure_ascii=False),
                    json.dumps(search.dictionaries, ensure_ascii=False),
                ),
            )
            .fetchall()
        )
        return tuple(self._term_record(row) for row in rows)

    def search_terms(self, search: TermSearch) -> tuple[TermRecord, ...]:
        if not search.forms or not search.forms[0] or search.limit < 1:
            return ()
        pattern = search.forms[0]
        query = _SEARCH_QUERY_ENRICHED if self._has_enriched_entries() else _SEARCH_QUERY_LEGACY
        rows = self._conn().execute(
            query,
            (
                json.dumps(search.dictionaries, ensure_ascii=False),
                pattern,
                pattern,
                json.dumps(search.dictionaries, ensure_ascii=False),
                search.limit,
            ),
        )
        return tuple(self._term_record(row) for row in rows)

    def exact_terms(self, forms: tuple[str, ...], dictionaries: tuple[str, ...]) -> frozenset[str]:
        values = tuple(dict.fromkeys(form for form in forms if form))
        if not values:
            return frozenset()
        rows = self._conn().execute(
            _EXACT_TERMS_QUERY,
            (json.dumps(values, ensure_ascii=False), *self._dictionary_args(dictionaries)),
        )
        return frozenset(row[0] for row in rows)

    def media_for(self, dictionary: str, paths: tuple[str, ...]) -> dict[str, bytes]:
        values = tuple(dict.fromkeys(path for path in paths if path))
        if not values:
            return {}
        rows = self._conn().execute(
            _MEDIA_QUERY,
            (dictionary, json.dumps(values, ensure_ascii=False)),
        )
        return dict(rows)

    def find_related(
        self, sequences: tuple[int, ...], dictionaries: tuple[str, ...]
    ) -> tuple[TermRecord, ...]:
        values = tuple(dict.fromkeys(sequence for sequence in sequences if sequence >= 0))
        if not values:
            return ()
        query = _RELATED_QUERY_ENRICHED if self._has_enriched_entries() else _RELATED_QUERY_LEGACY
        rows = (
            self._conn()
            .execute(
                query,
                (
                    json.dumps(dictionaries, ensure_ascii=False),
                    json.dumps(values),
                    json.dumps(dictionaries, ensure_ascii=False),
                ),
            )
            .fetchall()
        )
        return tuple(self._term_record(row) for row in rows)

    def _term_record(self, row: tuple[Any, ...]) -> TermRecord:
        dictionary_id, record_id = row[0], row[3]
        with self._entry_lock:
            cache = self._entry_caches.get(dictionary_id)
            cached = cache.get(record_id) if cache is not None else None
            if cached is not None:
                assert cache is not None
                cache.move_to_end(record_id)
        if cached is not None:
            if self._cache_observer is not None:
                self._cache_observer.hit()
            return cached
        if self._cache_observer is not None:
            self._cache_observer.miss()
        (
            did,
            title,
            order,
            record_id,
            term,
            reading,
            glossary,
            raw_tags,
            sequence,
            rules,
            score,
            term_tags,
        ) = row
        source = SourceTrace(title, order, record_id)
        tags = self._tags(did)
        definition_tags = self._split_tags(raw_tags, tags, source)
        content = json.loads(glossary or "[]")
        definition = Definition(
            tuple(content),
            definition_tags,
            source,
            score or 0,
            sequence if sequence is not None else -1,
        )
        record = TermRecord(
            term,
            reading,
            (definition,),
            source,
            definition_tags,
            self._split_tags(term_tags, tags, source),
            tuple((rules or "").split()),
            score or 0,
            sequence if sequence is not None else -1,
        )
        if self._entry_cache_max == 0:
            return record
        evicted = False
        with self._entry_lock:
            cache = self._entry_caches.setdefault(dictionary_id, OrderedDict())
            existing = cache.get(record_id)
            if existing is not None:
                cache.move_to_end(record_id)
                return existing
            cache[record_id] = record
            while len(cache) > self._entry_cache_max:
                cache.popitem(last=False)
                evicted = True
        if evicted and self._cache_observer is not None:
            self._cache_observer.eviction()
        return record

    def decoded_entry_count(self) -> int:
        with self._entry_lock:
            return sum(map(len, self._entry_caches.values()))

    def find_frequencies(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...]
    ) -> tuple[Frequency, ...]:
        terms = meta_lookup_terms(headwords)
        if not terms:
            return ()
        rows = self._conn().execute(
            _FREQUENCY_QUERY,
            (json.dumps(terms, ensure_ascii=False), *self._dictionary_args(dictionaries)),
        )
        return tuple(
            Frequency(title, rank if rank is not None else display or "", display, reading)
            for title, _term, reading, rank, display in prefer_term_keyed(rows, headwords)
        )

    def frequent_terms(
        self, limit: int, dictionaries: tuple[str, ...] = ()
    ) -> tuple[tuple[str, str], ...]:
        """The ``limit`` best-ranked ``(term, reading)`` across the given frequency dictionaries,
        most popular first, one row per term. ``limit <= 0`` means all of them.

        A term listed by several dictionaries is scored by its BEST rank, and SQLite's bare-column
        rule pairs the surviving ``MIN(rank)`` with that same row's reading — so the reading is the
        one the winning dictionary gave, not an arbitrary sibling's.
        """
        rows = self._conn().execute(
            _FREQUENT_TERMS_QUERY,
            (*self._dictionary_args(dictionaries), limit if limit > 0 else -1),
        )
        return tuple((term, reading or "") for term, reading, _rank in rows if term)

    def find_pronunciations(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...]
    ) -> tuple[Pronunciation, ...]:
        terms = meta_lookup_terms(headwords)
        if not terms:
            return ()
        rows = self._conn().execute(
            _PRONUNCIATION_QUERY,
            (json.dumps(terms, ensure_ascii=False), *self._dictionary_args(dictionaries)),
        )
        result: list[Pronunciation] = []
        for title, term, reading, mode, raw_positions in prefer_term_keyed(rows, headwords):
            payload = json.loads(raw_positions or "[]")
            if mode == "ipa":
                result.extend(
                    Pronunciation(title, reading or term, ipa=item.get("ipa"))
                    for item in payload
                    if isinstance(item, dict) and isinstance(item.get("ipa"), str)
                )
                continue
            for item in payload:
                if isinstance(item, int):
                    result.append(Pronunciation(title, reading or term, (item,)))
                elif isinstance(item, dict) and isinstance(item.get("p"), int):
                    result.append(
                        Pronunciation(
                            title,
                            reading or term,
                            (item["p"],),
                            nasal_morae=tuple(item.get("n", ())),
                            devoiced_morae=tuple(item.get("d", ())),
                        )
                    )
                elif isinstance(item, dict) and isinstance(item.get("position"), (int, str)):
                    result.append(
                        Pronunciation(
                            title,
                            reading or term,
                            (item["position"],),
                            nasal_morae=_morae(item.get("nasal")),
                            devoiced_morae=_morae(item.get("devoice")),
                        )
                    )
        return tuple(result)

    def find_kanji(
        self, characters: tuple[str, ...], dictionaries: tuple[str, ...]
    ) -> tuple[KanjiEntry, ...]:
        chars = tuple(dict.fromkeys(characters))
        if not chars:
            return ()
        rows = self._conn().execute(
            _KANJI_QUERY,
            (json.dumps(chars, ensure_ascii=False), *self._dictionary_args(dictionaries)),
        )
        frequencies: dict[str, list[Frequency]] = {}
        has_kanji_meta = (
            self._conn()
            .execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kanji_meta'")
            .fetchone()
        )
        if has_kanji_meta is not None:
            frequency_rows = self._conn().execute(
                _KANJI_FREQUENCY_QUERY,
                (json.dumps(chars, ensure_ascii=False), *self._dictionary_args(dictionaries)),
            )
            for title, character, raw_value in frequency_rows:
                value = parse_frequency(json.loads(raw_value))
                frequencies.setdefault(character, []).append(
                    Frequency(
                        title,
                        value.value if value.value is not None else value.display or "",
                        value.display,
                    )
                )
        result: list[KanjiEntry] = []
        for did, title, order, record_id, char, onyomi, kunyomi, raw_tags, meanings, stats in rows:
            source = SourceTrace(title, order, record_id)
            tag_map = self._tags(did)
            stat_values = tuple(json.loads(stats or "{}").items())
            result.append(
                KanjiEntry(
                    char,
                    tuple((onyomi or "").split()),
                    tuple((kunyomi or "").split()),
                    tuple(json.loads(meanings or "[]")),
                    self._split_tags(raw_tags, tag_map, source),
                    stat_values,
                    tuple(frequencies.get(char, ())),
                    source=source,
                    stat_tags=tuple(
                        (code, replace(tag_map.get(code, Tag(code)), source=source))
                        for code, _value in stat_values
                    ),
                )
            )
        return tuple(result)


def _morae(value: Any) -> tuple[int, ...]:
    if isinstance(value, int) and not isinstance(value, bool):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, int) and not isinstance(item, bool))
    return ()
