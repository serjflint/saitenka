from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yomitanlite.archive import ArchiveLimits, DictionaryArchive, DictionaryArchiveError
from yomitanlite.media import normalize_glossary
from yomitanlite.metadata import parse_frequency
from yomitanlite.models import Capability, DictionaryInfo

if TYPE_CHECKING:
    from collections.abc import Callable


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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dictionaries(
  id INTEGER PRIMARY KEY, title TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
  import_order INTEGER NOT NULL, source_name TEXT NOT NULL, revision TEXT NOT NULL,
  imported_at TEXT NOT NULL, schema_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS entries(
  dict_id INTEGER NOT NULL, id INTEGER NOT NULL, term TEXT NOT NULL, reading TEXT NOT NULL,
  glossary TEXT NOT NULL, tags TEXT NOT NULL, seq INTEGER, rules TEXT NOT NULL,
  score INTEGER NOT NULL, term_tags TEXT NOT NULL, PRIMARY KEY(dict_id, id));
CREATE TABLE IF NOT EXISTS keys(dict_id INTEGER NOT NULL, key TEXT NOT NULL, id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kanji(
  dict_id INTEGER NOT NULL, chr TEXT NOT NULL, onyomi TEXT NOT NULL, kunyomi TEXT NOT NULL,
  tags TEXT NOT NULL, meanings TEXT NOT NULL, stats TEXT NOT NULL, PRIMARY KEY(dict_id, chr));
CREATE TABLE IF NOT EXISTS term_meta(
  dict_id INTEGER NOT NULL, term TEXT NOT NULL, mode TEXT NOT NULL, reading TEXT,
  rank INTEGER, disp TEXT, positions TEXT);
CREATE TABLE IF NOT EXISTS kanji_meta(
  dict_id INTEGER NOT NULL, chr TEXT NOT NULL, mode TEXT NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags(
  dict_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, ord INTEGER NOT NULL,
  category TEXT NOT NULL, notes TEXT NOT NULL, score INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS media(
  dict_id INTEGER NOT NULL, path TEXT NOT NULL, png BLOB NOT NULL, PRIMARY KEY(dict_id, path));
CREATE INDEX IF NOT EXISTS idx_keys ON keys(key, dict_id);
CREATE INDEX IF NOT EXISTS idx_entries_seq ON entries(seq, dict_id);
CREATE INDEX IF NOT EXISTS idx_term_meta_term ON term_meta(term, mode, dict_id);
CREATE INDEX IF NOT EXISTS idx_kanji_char ON kanji(chr, dict_id);
"""

_LEGACY_MIGRATIONS = (
    (
        "PRAGMA table_info(dictionaries)",
        (
            ("kind", "ALTER TABLE dictionaries ADD COLUMN kind TEXT NOT NULL DEFAULT 'dict'"),
            (
                "import_order",
                "ALTER TABLE dictionaries ADD COLUMN import_order INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "source_name",
                "ALTER TABLE dictionaries ADD COLUMN source_name TEXT NOT NULL DEFAULT ''",
            ),
            (
                "revision",
                "ALTER TABLE dictionaries ADD COLUMN revision TEXT NOT NULL DEFAULT ''",
            ),
            (
                "imported_at",
                "ALTER TABLE dictionaries ADD COLUMN imported_at TEXT NOT NULL DEFAULT ''",
            ),
            (
                "schema_version",
                "ALTER TABLE dictionaries ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1",
            ),
        ),
    ),
    (
        "PRAGMA table_info(entries)",
        (
            ("rules", "ALTER TABLE entries ADD COLUMN rules TEXT NOT NULL DEFAULT ''"),
            ("score", "ALTER TABLE entries ADD COLUMN score INTEGER NOT NULL DEFAULT 0"),
            (
                "term_tags",
                "ALTER TABLE entries ADD COLUMN term_tags TEXT NOT NULL DEFAULT ''",
            ),
        ),
    ),
    (
        "PRAGMA table_info(tags)",
        (("score", "ALTER TABLE tags ADD COLUMN score INTEGER NOT NULL DEFAULT 0"),),
    ),
)


class DictionaryDatabase:
    """Yomitan dictionary administration; lookup is provided by SqliteDictionaryStore."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript(_SCHEMA)
            self._migrate_legacy_schema(connection)
            if connection.execute("SELECT COUNT(*) FROM schema_info").fetchone()[0] == 0:
                connection.execute("INSERT INTO schema_info VALUES (2)")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
        for pragma, migrations in _LEGACY_MIGRATIONS:
            present = {row[1] for row in connection.execute(pragma)}
            for name, statement in migrations:
                if name not in present:
                    connection.execute(statement)

    def import_dictionary(self, archive: str | Path | ImportRequest) -> DictionaryInfo:
        request = archive if isinstance(archive, ImportRequest) else ImportRequest(Path(archive))
        self.initialize()
        with DictionaryArchive(request.archive, request.limits) as source:
            info = self._dictionary_info(source)
            connection = sqlite3.connect(self.path)
            try:
                with connection:
                    self._remove(connection, info.title)
                    cursor = connection.execute(
                        "INSERT INTO dictionaries(title, kind, import_order, source_name, revision, "
                        "imported_at, schema_version) VALUES(?, ?, ?, ?, ?, ?, 2)",
                        (
                            info.title,
                            self._kind(source),
                            request.import_order,
                            request.archive.name,
                            info.revision,
                            request.imported_at or datetime.now(UTC).isoformat(),
                        ),
                    )
                    dictionary_id = int(cursor.lastrowid or 0)
                    self._load(source, connection, dictionary_id, request)
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

    @staticmethod
    def _kind(source: DictionaryArchive) -> str:
        if source.names("term_bank"):
            return "dict"
        modes = {
            entry[1]
            for name in source.names("term_meta_bank")
            for entry in source.read_bank(name)
            if isinstance(entry, list) and len(entry) > 1
        }
        return "pitch" if "pitch" in modes else "freq"

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
        media = source.media()
        media_by_path = dict(media)
        record_id = 0
        for completed, (kind, name) in enumerate(banks, 1):
            if request.is_cancelled is not None and request.is_cancelled():
                raise DictionaryArchiveError("dictionary import cancelled")
            records = source.read_bank(name)
            if kind == "term_bank":
                record_id = self._load_terms(
                    connection, dictionary_id, record_id, records, media_by_path
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
        if source.index.get("frequencyMode") == "occurrence-based":
            self._rank_occurrences(connection, dictionary_id)

    @staticmethod
    def _rank_occurrences(connection: sqlite3.Connection, dictionary_id: int) -> None:
        rows = connection.execute(
            "SELECT rowid, rank FROM term_meta "
            "WHERE dict_id=? AND mode='freq' AND rank IS NOT NULL",
            (dictionary_id,),
        ).fetchall()
        ranks = {
            value: rank
            for rank, value in enumerate(
                sorted({value for _rowid, value in rows}, reverse=True),
                1,
            )
        }
        connection.executemany(
            "UPDATE term_meta SET rank=? WHERE rowid=?",
            ((ranks[value], rowid) for rowid, value in rows),
        )

    @staticmethod
    def _load_terms(
        connection: sqlite3.Connection,
        dictionary_id: int,
        record_id: int,
        records: list[Any],
        media: dict[str, bytes],
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
                    entry[6] if len(entry) > 6 and isinstance(entry[6], int) else None,
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
                positions = json.dumps(data.get("pitches", ()), ensure_ascii=False)
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
