from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from saitenka_dict.models import (
    Capability,
    Headword,
    KanjiQuery,
    KanjiResult,
    MatchSource,
    SearchQuery,
    Tag,
    TermEntry,
    TermQuery,
    TermResult,
    TermResultMode,
)
from saitenka_dict.store import DictionaryStore, TermRecord, TermSearch


@dataclass(frozen=True, slots=True)
class _Match:
    record: TermRecord
    text: str
    length: int


@dataclass(frozen=True, slots=True)
class _TagIdentity:
    name: str
    category: str
    notes: str
    order: int
    score: int
    dictionary: str

    @classmethod
    def from_tag(cls, tag: Tag) -> _TagIdentity:
        return cls(
            name=tag.name,
            category=tag.category,
            notes=tag.notes,
            order=tag.order,
            score=tag.score,
            dictionary=tag.source.dictionary if tag.source else "",
        )


class Translator:
    """Application-neutral assembly of dictionary rows into Yomitan result modes."""

    capabilities = frozenset(
        {
            Capability.TERM_LOOKUP,
            Capability.KANJI_LOOKUP,
            Capability.SEARCH,
            Capability.MEDIA,
            Capability.ATTESTATION,
        }
    )

    def __init__(self, store: DictionaryStore):
        self.store = store

    def lookup_terms(self, query: TermQuery) -> TermResult:
        if not query.text or query.max_results < 1:
            return TermResult((), len(query.text), 0)
        matches, matched_length = self._matches(query)
        entries = self._assemble(matches, query.mode, query)
        return TermResult(tuple(entries[: query.max_results]), len(query.text), matched_length)

    def lookup_kanji(self, query: KanjiQuery) -> KanjiResult:
        characters = tuple(dict.fromkeys(query.text))
        return KanjiResult(self.store.find_kanji(characters, query.dictionaries))

    def search_terms(self, query: SearchQuery) -> TermResult:
        records = self.store.search_terms(
            TermSearch((query.pattern,), query.dictionaries, query.max_results)
        )
        matches = tuple(
            _Match(record, record.term, len(record.term))
            for record in sorted(
                records, key=lambda item: self._record_order(item, query.dictionaries)
            )
        )
        term_query = TermQuery(
            query.pattern,
            mode=TermResultMode.SPLIT,
            dictionaries=query.dictionaries,
            max_results=query.max_results,
        )
        entries = self._assemble(matches, TermResultMode.SPLIT, term_query)
        return TermResult(tuple(entries[: query.max_results]), len(query.pattern), 0)

    def exact_terms(
        self, forms: tuple[str, ...], dictionaries: tuple[str, ...] = ()
    ) -> frozenset[str]:
        return self.store.exact_terms(forms, dictionaries)

    def media_for(self, dictionary: str, paths: tuple[str, ...]) -> dict[str, bytes]:
        return self.store.media_for(dictionary, paths)

    def frequencies_for(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...] = ()
    ):
        return self.store.find_frequencies(headwords, dictionaries)

    def pronunciations_for(
        self, headwords: tuple[tuple[str, str], ...], dictionaries: tuple[str, ...] = ()
    ):
        return self.store.find_pronunciations(headwords, dictionaries)

    def _matches(self, query: TermQuery) -> tuple[tuple[_Match, ...], int]:
        for length in range(len(query.text), 0, -1):
            text = query.text[:length]
            forms = tuple(
                dict.fromkeys(
                    (
                        text,
                        *query.alternate_forms,
                        *(item.source for item in query.inflections),
                    )
                )
            )
            records = self.store.find_terms(
                TermSearch(forms, query.dictionaries, query.max_results)
            )
            if records:
                ordered = sorted(
                    records,
                    key=lambda record: self._record_order(record, query.dictionaries),
                )
                return tuple(_Match(record, text, length) for record in ordered), length
        return (), 0

    @staticmethod
    def _record_order(record: TermRecord, dictionaries: tuple[str, ...]) -> tuple[int, int, int]:
        priorities = {title: index for index, title in enumerate(dictionaries)}
        dictionary_order = priorities.get(record.source.dictionary, record.source.dictionary_index)
        return dictionary_order, -record.score, record.source.record_id or 0

    def _assemble(
        self, matches: tuple[_Match, ...], mode: TermResultMode, query: TermQuery
    ) -> list[TermEntry]:
        if mode in {TermResultMode.SIMPLE, TermResultMode.SPLIT}:
            return [self._entry((match,), query) for match in matches]
        key = self._group_key(mode)
        grouped: dict[object, list[_Match]] = defaultdict(list)
        for match in matches:
            grouped[key(match.record)].append(match)
        if mode is TermResultMode.MERGE:
            sequences = tuple(
                {match.record.sequence for match in matches if match.record.sequence >= 0}
            )
            for record in self.store.find_related(sequences, query.dictionaries):
                bucket = grouped[key(record)]
                if all(match.record != record for match in bucket):
                    inherited = bucket[0] if bucket else _Match(record, query.text, len(query.text))
                    bucket.append(_Match(record, inherited.text, inherited.length))
        return [self._entry(tuple(group), query) for group in grouped.values()]

    @staticmethod
    def _group_key(mode: TermResultMode):
        if mode is TermResultMode.TERM:
            return lambda record: record.term
        if mode is TermResultMode.MERGE:
            return lambda record: (
                record.sequence if record.sequence >= 0 else (record.term, record.reading)
            )
        return lambda record: (record.term, record.reading)

    def _entry(self, matches: tuple[_Match, ...], query: TermQuery) -> TermEntry:
        headwords: list[Headword] = []
        grouped: dict[tuple[str, str], list[_Match]] = defaultdict(list)
        for match in matches:
            record = match.record
            grouped[record.term, record.reading].append(match)
        for (term, reading), headword_matches in grouped.items():
            unique_tags: dict[_TagIdentity, Tag] = {}
            for match in headword_matches:
                for tag in match.record.term_tags:
                    unique_tags.setdefault(_TagIdentity.from_tag(tag), tag)
            tags = tuple(
                sorted(
                    unique_tags.values(),
                    key=lambda tag: (tag.order, tag.name),
                )
            )
            sources = tuple(
                dict.fromkeys(
                    MatchSource(
                        match.text,
                        match.text,
                        term,
                        match.length,
                        match_source="term" if term == match.text else "reading",
                    )
                    for match in headword_matches
                )
            )
            headwords.append(Headword(term, reading, tags, sources))
        records = tuple(match.record for match in matches)
        pairs = tuple((headword.term, headword.reading) for headword in headwords)
        definitions = tuple(definition for record in records for definition in record.definitions)
        frequencies = self.store.find_frequencies(pairs, query.dictionaries)
        pronunciations = self.store.find_pronunciations(pairs, query.dictionaries)
        primary = query.primary_reading is None or any(
            headword.reading == query.primary_reading for headword in headwords
        )
        return TermEntry(
            tuple(headwords),
            definitions,
            query.inflections,
            frequencies,
            pronunciations,
            records[0].sequence,
            max(record.score for record in records),
            primary,
            max(match.length for match in matches),
        )
