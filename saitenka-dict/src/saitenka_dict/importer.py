from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saitenka_dict.archive import (
    PRIMARY_ORDER,
    ArchiveLimits,
    DictionaryArchive,
    DictionaryArchiveError,
    zip_roles,
)
from saitenka_dict.media import normalize_glossary
from saitenka_dict.metadata import parse_frequency
from saitenka_dict.models import Capability, DictionaryInfo
from saitenka_dict.schema import SCHEMA_VERSION, ensure_schema

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportProgress:
    bank: str
    completed: int
    total: int


@dataclass(frozen=True, slots=True)
class ImportRequest:
    archive: Path
    import_order: int = 0
    imported_at: str | None = None
    limits: ArchiveLimits = field(default_factory=ArchiveLimits)
    on_progress: Callable[[ImportProgress], None] | None = None
    is_cancelled: Callable[[], bool] | None = None
    #: ``(name, bytes) -> png | None``, applied to SVG members. Injected because the renderer, its
    #: optional extra, and the fallback font are the application's choices; ``None`` stores them raw.
    rasterize_svg: Callable[[str, bytes], bytes | None] | None = None
    #: Whether to store each entry's Yomitan ``seq`` — only useful to a consumer showing related terms.
    persist_seq: bool = True


_SVG_SUFFIX = ".svg"


def _rasterized(
    media: tuple[tuple[str, bytes], ...],
    rasterize: Callable[[str, bytes], bytes | None] | None,
) -> tuple[tuple[str, bytes], ...]:
    """Replace SVG members with their rasterized bytes. A member the rasterizer declines is dropped,
    not stored: half an image is worse than the renderer's missing-glyph fallback."""
    if rasterize is None:
        return media
    result = []
    failed = 0
    for name, data in media:
        if not name.lower().endswith(_SVG_SUFFIX):
            result.append((name, data))
            continue
        raster = rasterize(name, data)
        if raster is None:
            failed += 1
            continue
        result.append((name, raster))
    if failed:
        # A per-dictionary tally beside the per-file warnings: one bad glyph is noise, hundreds is a
        # broken rasterizer, and only the count distinguishes them.
        log.warning("%d media SVG(s) failed to rasterize — those render as a fallback box", failed)
    return tuple(result)


def _morae(value: Any) -> list[int]:
    """A NHK/Kanjium per-mora annotation (``devoice``/``nasal``) → 1-based mora indices. Yomitan
    writes an int for one mora or an array for several; a bare bool flag carries no index."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]
    return []


def _accents(data: dict[str, Any]) -> list[int | dict[str, Any]]:
    """One pitch entry's ``pitches`` → the stored accent list.

    A plain accent (no devoice/nasal anywhere) stores the bare ``int`` downstep position it always
    has, so an ordinary pitch dictionary's rows are byte-identical to previous releases'. Only an
    entry that actually carries annotations grows into ``{"p", "d", "n"}``.
    """
    accents: list[int | dict[str, Any]] = []
    for pitch in data.get("pitches", ()):
        if not (isinstance(pitch, dict) and isinstance(position := pitch.get("position"), int)):
            continue
        devoiced, nasal = _morae(pitch.get("devoice")), _morae(pitch.get("nasal"))
        accents.append(
            {"p": position, "d": devoiced, "n": nasal} if (devoiced or nasal) else position
        )
    return accents


class DictionaryDatabase:
    """Yomitan dictionary administration; lookup is provided by SqliteDictionaryStore."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            ensure_schema(connection)
        finally:
            connection.close()

    def import_dictionary(self, archive: str | Path | ImportRequest) -> DictionaryInfo:
        request = archive if isinstance(archive, ImportRequest) else ImportRequest(Path(archive))
        self.initialize()
        # A zip can fill several roles at once (a combined definition+frequency dictionary), so the
        # display `kind` is only the primary one — every role's banks are loaded regardless.
        roles = zip_roles(request.archive)
        with DictionaryArchive(request.archive, request.limits) as source:
            info = self._dictionary_info(source)
            connection = sqlite3.connect(self.path)
            try:
                connection.execute("PRAGMA synchronous=NORMAL")
                with connection:
                    self._remove(connection, info.title)
                    cursor = connection.execute(
                        "INSERT INTO dictionaries(title, kind, import_order, source_name, revision, "
                        "imported_at, schema_version) VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (
                            info.title,
                            next(kind for kind in PRIMARY_ORDER if kind in roles),
                            request.import_order,
                            request.archive.name,
                            info.revision,
                            request.imported_at or datetime.now(UTC).isoformat(),
                            SCHEMA_VERSION,
                        ),
                    )
                    dictionary_id = int(cursor.lastrowid or 0)
                    self._load(source, connection, dictionary_id, request)
                if roles & {"freq", "pitch"}:
                    # term_meta's planner statistics decide whether the pitch/frequency lookups seek
                    # or scan, and a fresh import moves the row count by millions.
                    connection.execute("ANALYZE term_meta")
                    connection.commit()
            finally:
                connection.close()
        return info

    def list_dictionaries(self) -> tuple[DictionaryInfo, ...]:
        self.initialize()
        connection = sqlite3.connect(self.path)
        try:
            rows = connection.execute(
                "SELECT id, title, revision FROM dictionaries ORDER BY import_order, id"
            ).fetchall()
            result = []
            for dictionary_id, title, revision in rows:
                capabilities = {Capability.IMPORT}
                if connection.execute(
                    "SELECT 1 FROM entries WHERE dict_id=? LIMIT 1", (dictionary_id,)
                ).fetchone():
                    capabilities.add(Capability.TERM_LOOKUP)
                if connection.execute(
                    "SELECT 1 FROM kanji WHERE dict_id=? LIMIT 1", (dictionary_id,)
                ).fetchone():
                    capabilities.add(Capability.KANJI_LOOKUP)
                if connection.execute(
                    "SELECT 1 FROM media WHERE dict_id=? LIMIT 1", (dictionary_id,)
                ).fetchone():
                    capabilities.add(Capability.MEDIA)
                result.append(DictionaryInfo(title, revision, capabilities=frozenset(capabilities)))
            return tuple(result)
        finally:
            connection.close()

    def remove_dictionary(self, title: str) -> bool:
        self.initialize()
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                exists = connection.execute(
                    "SELECT 1 FROM dictionaries WHERE title=?", (title,)
                ).fetchone()
                self._remove(connection, title)
            return exists is not None
        finally:
            connection.close()

    @staticmethod
    def _remove(connection: sqlite3.Connection, title: str) -> None:
        row = connection.execute("SELECT id FROM dictionaries WHERE title=?", (title,)).fetchone()
        if row is None:
            return
        dictionary_id = row[0]
        connection.execute("DELETE FROM entries WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM keys WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM kanji WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM term_meta WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM kanji_meta WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM tags WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM media WHERE dict_id=?", (dictionary_id,))
        connection.execute("DELETE FROM dictionaries WHERE id=?", (dictionary_id,))

    @staticmethod
    def _dictionary_info(source: DictionaryArchive) -> DictionaryInfo:
        capabilities = {Capability.IMPORT}
        if source.names("term_bank"):
            capabilities.add(Capability.TERM_LOOKUP)
        if source.names("kanji_bank"):
            capabilities.add(Capability.KANJI_LOOKUP)
        if source.media():
            capabilities.add(Capability.MEDIA)
        return DictionaryInfo(
            str(source.index["title"]),
            str(source.index.get("revision") or ""),
            int(source.index.get("format") or source.index.get("version") or 3),
            frozenset(capabilities),
        )

    def _load(
        self,
        source: DictionaryArchive,
        connection: sqlite3.Connection,
        dictionary_id: int,
        request: ImportRequest,
    ) -> None:
        banks = tuple(
            (kind, name)
            for kind in ("term_bank", "kanji_bank", "term_meta_bank", "kanji_meta_bank", "tag_bank")
            for name in source.names(kind)
        )
        media = _rasterized(source.media(), request.rasterize_svg)
        media_by_path = dict(media)
        record_id = 0
        for completed, (kind, name) in enumerate(banks, 1):
            if request.is_cancelled is not None and request.is_cancelled():
                raise DictionaryArchiveError("dictionary import cancelled")
            records = source.read_bank(name)
            if kind == "term_bank":
                record_id = self._load_terms(
                    connection,
                    dictionary_id,
                    record_id,
                    records,
                    media_by_path,
                    persist_seq=request.persist_seq,
                )
            elif kind == "kanji_bank":
                self._load_kanji(connection, dictionary_id, records)
            elif kind == "term_meta_bank":
                self._load_term_meta(connection, dictionary_id, records)
            elif kind == "kanji_meta_bank":
                self._load_kanji_meta(connection, dictionary_id, records)
            else:
                self._load_tags(connection, dictionary_id, records)
            if request.on_progress is not None:
                request.on_progress(ImportProgress(name, completed, len(banks)))
        connection.executemany(
            "INSERT INTO media(dict_id, path, png) VALUES(?, ?, ?)",
            ((dictionary_id, name, data) for name, data in media),
        )
        occurrence_based = source.index.get("frequencyMode") == "occurrence-based"
        if occurrence_based:
            self._rank_occurrences(connection, dictionary_id)
        # The ORIGINAL frequency mode, which nothing downstream can reconstruct once the counts have
        # been ranked. An occurrence-based dictionary's rank is dense and per-corpus, so it is not
        # comparable with a real rank-based list and the blended-frequency pill must exclude it.
        connection.execute(
            "INSERT OR REPLACE INTO meta VALUES(?, ?)",
            (
                f"freqmode:{dictionary_id}",
                "occurrence" if occurrence_based else "rank",
            ),
        )

    @staticmethod
    def _rank_occurrences(connection: sqlite3.Connection, dictionary_id: int) -> None:
        """Occurrence COUNTS → 1-based dense ranks (most frequent = 1).

        Every consumer assumes rank semantics, so left as counts the dictionary colours inverted. The
        original count survives in ``disp`` when the dictionary supplied no explicit ``displayValue``,
        so the tooltip still shows the real frequency rather than the derived rank.
        """
        rows = connection.execute(
            "SELECT rowid, rank, disp FROM term_meta "
            "WHERE dict_id=? AND mode='freq' AND rank IS NOT NULL",
            (dictionary_id,),
        ).fetchall()
        ranks = {
            value: rank
            for rank, value in enumerate(
                sorted({value for _rowid, value, _disp in rows}, reverse=True),
                1,
            )
        }
        connection.executemany(
            "UPDATE term_meta SET rank=?, disp=? WHERE rowid=?",
            (
                (ranks[value], disp if disp is not None else str(value), rowid)
                for rowid, value, disp in rows
            ),
        )

    @staticmethod
    def _load_terms(
        connection: sqlite3.Connection,
        dictionary_id: int,
        record_id: int,
        records: list[Any],
        media: dict[str, bytes],
        *,
        persist_seq: bool = True,
    ) -> int:
        rows: list[tuple[Any, ...]] = []
        keys: list[tuple[int, str, int]] = []
        for entry in records:
            if not isinstance(entry, list) or len(entry) < 6 or not isinstance(entry[0], str):
                raise DictionaryArchiveError("invalid term bank entry")
            record_id += 1
            term = entry[0]
            reading = entry[1] or term
            rows.append(
                (
                    dictionary_id,
                    record_id,
                    term,
                    reading,
                    json.dumps(normalize_glossary(entry[5], media), ensure_ascii=False),
                    entry[2] or "",
                    entry[6]
                    if persist_seq and len(entry) > 6 and isinstance(entry[6], int)
                    else None,
                    entry[3] or "",
                    entry[4] if isinstance(entry[4], int) else 0,
                    entry[7] if len(entry) > 7 and isinstance(entry[7], str) else "",
                )
            )
            keys.append((dictionary_id, term, record_id))
            if reading != term:
                keys.append((dictionary_id, reading, record_id))
        connection.executemany(
            "INSERT INTO entries(dict_id, id, term, reading, glossary, tags, seq, rules, score, "
            "term_tags) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.executemany("INSERT INTO keys VALUES(?, ?, ?)", keys)
        return record_id

    @staticmethod
    def _load_kanji(connection: sqlite3.Connection, dictionary_id: int, records: list[Any]) -> None:
        rows = []
        for entry in records:
            if not isinstance(entry, list) or not entry or not isinstance(entry[0], str):
                raise DictionaryArchiveError("invalid kanji bank entry")
            rows.append(
                (
                    dictionary_id,
                    entry[0],
                    entry[1] or "",
                    entry[2] or "",
                    entry[3] or "",
                    json.dumps(entry[4] if len(entry) > 4 else [], ensure_ascii=False),
                    json.dumps(entry[5] if len(entry) > 5 else {}, ensure_ascii=False),
                )
            )
        connection.executemany("INSERT OR REPLACE INTO kanji VALUES(?, ?, ?, ?, ?, ?, ?)", rows)

    @staticmethod
    def _load_term_meta(
        connection: sqlite3.Connection, dictionary_id: int, records: list[Any]
    ) -> None:
        rows = []
        for entry in records:
            if (
                not isinstance(entry, list)
                or len(entry) < 3
                or entry[1]
                not in {
                    "freq",
                    "ipa",
                    "pitch",
                }
            ):
                raise DictionaryArchiveError("invalid term metadata entry")
            term, mode, data = entry[:3]
            reading: str | None = None
            rank: int | float | None = None
            display: str | None = None
            positions: str | None = None
            if mode == "pitch" and isinstance(data, dict):
                reading = data.get("reading") or term
                accents = _accents(data)
                if not accents:
                    continue
                positions = json.dumps(accents, ensure_ascii=False)
            elif mode == "ipa" and isinstance(data, dict):
                reading = data.get("reading") or term
                positions = json.dumps(data.get("transcriptions", ()), ensure_ascii=False)
            else:
                frequency = parse_frequency(data)
                reading, rank, display = frequency.reading, frequency.value, frequency.display
            rows.append((dictionary_id, term, mode, reading, rank, display, positions))
        connection.executemany("INSERT INTO term_meta VALUES(?, ?, ?, ?, ?, ?, ?)", rows)

    @staticmethod
    def _load_kanji_meta(
        connection: sqlite3.Connection, dictionary_id: int, records: list[Any]
    ) -> None:
        rows = []
        for entry in records:
            if not isinstance(entry, list) or len(entry) < 3 or not isinstance(entry[0], str):
                raise DictionaryArchiveError("invalid kanji metadata entry")
            rows.append(
                (dictionary_id, entry[0], str(entry[1]), json.dumps(entry[2], ensure_ascii=False))
            )
        connection.executemany("INSERT INTO kanji_meta VALUES(?, ?, ?, ?)", rows)

    @staticmethod
    def _load_tags(connection: sqlite3.Connection, dictionary_id: int, records: list[Any]) -> None:
        rows = []
        for entry in records:
            if not isinstance(entry, list) or not entry or not isinstance(entry[0], str):
                raise DictionaryArchiveError("invalid tag bank entry")
            code = entry[0]
            rows.append(
                (
                    dictionary_id,
                    code,
                    code,
                    int(entry[2]) if len(entry) > 2 else 0,
                    str(entry[1]) if len(entry) > 1 else "",
                    str(entry[3]) if len(entry) > 3 else "",
                    int(entry[4]) if len(entry) > 4 else 0,
                )
            )
        connection.executemany(
            "INSERT INTO tags(dict_id, code, name, ord, category, notes, score) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
