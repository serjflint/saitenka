"""The consolidated dictionary database — every imported Yomitan dictionary in ONE SQLite file.

Unlike the old per-zip cache, this file is **primary data, not a regenerable cache**: dictionaries are
built into it once, at explicit **import** time (:meth:`DictionaryDb.import_zip`), the way Yomitan
imports a dictionary into its IndexedDB. After import the source zip is never read again, so it need
not be kept around. At runtime the overlay only ever **opens** this DB read-only — it never builds.

One file at ``data_dir()/dictionaries.sqlite`` (``%LOCALAPPDATA%\\saitenka`` / ``~/.local/share/saitenka``),
in **WAL** mode so several mpv instances can read it concurrently while an occasional import writes.

Every data row is tagged by ``dict_id`` (→ :data:`dictionaries`), so re-importing one dictionary is a
delete-by-title + insert in a single transaction and never disturbs the others. Definition dictionaries
land in ``entries`` / ``keys`` / ``kanji`` / ``tags``; frequency and pitch dictionaries land in
``term_meta`` (mode-tagged). The classification is by CONTENT (:func:`overlay.app.yomitan_import.classify_zip`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from overlay.app import paths

log = logging.getLogger(__name__)

DB_SCHEMA = 1  # bump to force a from-scratch re-import (the DB is dropped and rebuilt)

# Overridable default DB path — tests point this at a tmp file (mirrors the old CACHE_DIR override).
_DB_PATH_OVERRIDE: Path | None = None


def default_db_path() -> Path:
    return paths.data_dir() / "dictionaries.sqlite"


def db_path() -> Path:
    """The consolidated DB path: the test/env override if set, else the platform data-dir default."""
    return _DB_PATH_OVERRIDE or default_db_path()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS dictionaries(
  id INTEGER PRIMARY KEY, title TEXT UNIQUE, kind TEXT, import_order INTEGER,
  source_name TEXT, revision TEXT, imported_at TEXT, schema_version INTEGER);
CREATE TABLE IF NOT EXISTS entries(
  dict_id INTEGER, id INTEGER, term TEXT, reading TEXT, glossary TEXT, tags TEXT,
  PRIMARY KEY(dict_id, id));
CREATE TABLE IF NOT EXISTS keys(dict_id INTEGER, key TEXT, id INTEGER);
CREATE TABLE IF NOT EXISTS kanji(
  dict_id INTEGER, chr TEXT, onyomi TEXT, kunyomi TEXT, tags TEXT, meanings TEXT, stats TEXT,
  PRIMARY KEY(dict_id, chr));
CREATE TABLE IF NOT EXISTS term_meta(
  dict_id INTEGER, term TEXT, mode TEXT, reading TEXT, rank INTEGER, disp TEXT, positions TEXT);
CREATE TABLE IF NOT EXISTS tags(dict_id INTEGER, code TEXT, name TEXT, ord INTEGER);
CREATE INDEX IF NOT EXISTS idx_keys ON keys(dict_id, key);
CREATE INDEX IF NOT EXISTS idx_meta_term ON term_meta(dict_id, term);
"""


@dataclass(frozen=True)
class DictRow:
    """A row of the ``dictionaries`` table — one imported dictionary."""

    id: int
    title: str
    kind: str  # 'dict' | 'freq' | 'pitch'
    import_order: int
    source_name: str
    revision: str


def _title_of(zf: zipfile.ZipFile, fallback: str) -> str:
    try:
        return json.loads(zf.read("index.json")).get("title", fallback) or fallback
    except Exception:
        log.debug("index.json title read failed", exc_info=True)
        return fallback


def _revision_of(zf: zipfile.ZipFile) -> str:
    try:
        return str(json.loads(zf.read("index.json")).get("revision", "") or "")
    except Exception:
        return ""


def _read_term_meta(
    zf: zipfile.ZipFile,
) -> Iterable[tuple[str, str, str | None, int | None, str | None, str | None]]:
    """Yield ``(term, mode, reading, rank, disp, positions_json)`` over every freq/pitch term_meta entry.

    Covers the freq value-shapes (plain int, ``{"value"/"displayValue"}``, ``{"reading","frequency"}``,
    and the JLPT ``{"frequency": {"value": -1, "displayValue": "N5"}}`` form) and pitch entries
    (``{"reading", "pitches": [{"position": n}]}``) — the same shapes ``wordlists`` parses at read time.
    """
    from overlay.app.wordlists import read_json_bank

    for name in sorted(zf.namelist()):
        if not (name.startswith("term_meta_bank") and name.endswith(".json")):
            continue
        for entry in read_json_bank(zf, name) or []:
            if len(entry) < 3 or not isinstance(entry[1], str):
                continue
            term, mode, data = entry[0], entry[1], entry[2]
            if mode == "freq":
                reading, rank, disp = None, None, None
                if isinstance(data, (int, float)):
                    rank = int(data)
                elif isinstance(data, dict):
                    reading = data.get("reading")
                    fval = data.get("frequency", data)
                    if isinstance(fval, dict):
                        rank = fval.get("value")
                        disp = fval.get("displayValue")
                    elif isinstance(fval, (int, float)):
                        rank = int(fval)
                yield term, "freq", reading, rank, disp, None
            elif mode == "pitch":
                if not isinstance(data, dict):
                    continue
                reading = data.get("reading") or term
                positions = [
                    pos
                    for p in data.get("pitches", [])
                    if isinstance(p, dict) and isinstance(pos := p.get("position"), int)
                ]
                if not positions:
                    continue
                yield term, "pitch", reading, None, None, json.dumps(positions)


def _extract_tags(zf: zipfile.ZipFile) -> list[tuple[str, str, int]]:
    """Yomitan ``tag_bank_*.json`` → [(code, display_name, order)] for defTag pills (★ / priority form)."""
    from overlay.app.wordlists import read_json_bank

    out: list[tuple[str, str, int]] = []
    for name in sorted(zf.namelist()):
        if name.startswith("tag_bank") and name.endswith(".json"):
            out.extend(
                (
                    t[0],
                    t[0],
                    int(t[2]) if len(t) > 2 else 0,
                )  # [name, category, order, notes, score]
                for t in read_json_bank(zf, name) or []
                if t and isinstance(t[0], str)
            )
    return out


class DictionaryDb:
    """Read/write handle to the consolidated dictionary DB.

    Read connections are per-thread and read-only (safe parallel lookups); the single write connection
    is used only by :meth:`import_zip`. WAL mode lets readers proceed during an import.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()

    # --- lifecycle ----------------------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path | None = None) -> DictionaryDb:
        """Open (creating + schema-initialising if needed) the consolidated DB."""
        db = cls(path or db_path())
        db.path.parent.mkdir(parents=True, exist_ok=True)
        db._ensure_schema()
        return db

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")  # persists in the DB header; enables ro readers
            conn.executescript(_SCHEMA_SQL)
            row = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
            if row is None:
                conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(DB_SCHEMA),))
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        """A per-thread read-only connection (mmap'd, roomy page cache) for lookups."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            # mmap the DB (1 GiB — under SQLITE_MAX_MMAP_SIZE on every platform) so cold lookups hit
            # page-cache-backed memory instead of pread syscalls; 64 MiB page cache (negative = KiB).
            c.execute("PRAGMA mmap_size=1073741824")
            c.execute("PRAGMA cache_size=-65536")
            self._local.conn = c
        return c

    # --- import (the only build path) --------------------------------------------------------

    def import_zip(
        self,
        zip_path: str | Path,
        *,
        imported_at: str,
        import_order: int = 0,
        on_bank: Callable[[int, int], None] | None = None,
    ) -> DictRow:
        """Import one Yomitan dictionary zip into the DB, replacing any prior import of the same title.

        Classifies by content, reads the zip once, and writes the ``dictionaries`` row plus the data
        tables in a single transaction (so a mid-import failure leaves the DB untouched). ``on_bank``,
        if given, is called ``(done, total)`` per bank for progress. Returns the new :class:`DictRow`.
        """
        from overlay.app.yomitan_import import classify_zip

        zp = Path(zip_path)
        kind = classify_zip(zp)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
            # One transaction (conn) wrapping one zip read — commit on success, rollback on any error.
            with conn, zipfile.ZipFile(zp) as zf:
                title = _title_of(zf, zp.stem)
                self._drop_title(conn, title)
                cur = conn.execute(
                    "INSERT INTO dictionaries(title, kind, import_order, source_name, revision, "
                    "imported_at, schema_version) VALUES(?,?,?,?,?,?,?)",
                    (title, kind, import_order, zp.name, _revision_of(zf), imported_at, DB_SCHEMA),
                )
                did = int(cur.lastrowid or 0)
                if kind == "dict":
                    self._load_dict_banks(conn, zf, did, on_bank)
                else:  # 'freq' | 'pitch'
                    self._load_meta_banks(conn, zf, did, on_bank)
            row = self._row_by_id(conn, did)
        finally:
            conn.close()
        return row

    def _load_dict_banks(
        self,
        conn: sqlite3.Connection,
        zf: zipfile.ZipFile,
        did: int,
        on_bank: Callable[[int, int], None] | None,
    ) -> None:
        from overlay.app.wordlists import read_json_bank

        names = sorted(zf.namelist())
        term_banks = [n for n in names if n.startswith("term_bank") and n.endswith(".json")]
        kanji_banks = [n for n in names if n.startswith("kanji_bank") and n.endswith(".json")]
        total = len(term_banks) + len(kanji_banks)
        done = 0
        rid = 0
        for name in term_banks:
            bank = read_json_bank(zf, name)  # tolerant of wrong-CRC Yomitan zips (data intact)
            done += 1
            if on_bank:
                on_bank(done, total)
            if bank is None:
                continue
            rows, keys = [], []
            for e in bank:  # [term, reading, defTags, rules, score, glossary[], seq, termTags]
                rid += 1
                term, reading = e[0], e[1] or e[0]
                rows.append((did, rid, term, reading, json.dumps(e[5], ensure_ascii=False), e[2]))
                keys.append((did, term, rid))
                if reading != term:
                    keys.append((did, reading, rid))
            conn.executemany("INSERT INTO entries VALUES(?,?,?,?,?,?)", rows)
            conn.executemany("INSERT INTO keys VALUES(?,?,?)", keys)
        for name in kanji_banks:  # [char, onyomi, kunyomi, tags, meanings[], stats{}]
            bank = read_json_bank(zf, name)
            done += 1
            if on_bank:
                on_bank(done, total)
            if bank is None:
                continue
            krows = [
                (
                    did,
                    e[0],
                    e[1] or "",
                    e[2] or "",
                    e[3] or "",
                    json.dumps(e[4] if len(e) > 4 else [], ensure_ascii=False),
                    json.dumps(e[5] if len(e) > 5 else {}, ensure_ascii=False),
                )
                for e in bank
                if e and isinstance(e[0], str)
            ]
            conn.executemany("INSERT OR IGNORE INTO kanji VALUES(?,?,?,?,?,?,?)", krows)
        conn.executemany(
            "INSERT INTO tags VALUES(?,?,?,?)",
            [(did, code, name, order) for code, name, order in _extract_tags(zf)],
        )

    def _load_meta_banks(
        self,
        conn: sqlite3.Connection,
        zf: zipfile.ZipFile,
        did: int,
        on_bank: Callable[[int, int], None] | None,
    ) -> None:
        if on_bank:
            on_bank(0, 1)
        conn.executemany(
            "INSERT INTO term_meta VALUES(?,?,?,?,?,?,?)",
            [(did, *rest) for rest in _read_term_meta(zf)],
        )
        if on_bank:
            on_bank(1, 1)

    # --- queries -------------------------------------------------------------------------------

    def _drop_title(self, conn: sqlite3.Connection, title: str) -> None:
        row = conn.execute("SELECT id FROM dictionaries WHERE title=?", (title,)).fetchone()
        if row is None:
            return
        did = row[0]
        for table in ("entries", "keys", "kanji", "term_meta", "tags"):
            conn.execute(f"DELETE FROM {table} WHERE dict_id=?", (did,))
        conn.execute("DELETE FROM dictionaries WHERE id=?", (did,))

    def drop(self, title: str) -> bool:
        """Remove an imported dictionary by title. Returns True if it existed."""
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                existed = (
                    conn.execute("SELECT 1 FROM dictionaries WHERE title=?", (title,)).fetchone()
                    is not None
                )
                self._drop_title(conn, title)
            return existed
        finally:
            conn.close()

    @staticmethod
    def _row_from(r: Sequence) -> DictRow:
        return DictRow(int(r[0]), r[1], r[2], int(r[3] or 0), r[4] or "", r[5] or "")

    def _row_by_id(self, conn: sqlite3.Connection, did: int) -> DictRow:
        r = conn.execute(
            "SELECT id, title, kind, import_order, source_name, revision FROM dictionaries WHERE id=?",
            (did,),
        ).fetchone()
        return self._row_from(r)

    def list_dictionaries(self) -> list[DictRow]:
        """Every imported dictionary, ordered by import_order then id."""
        rows = self._conn().execute(
            "SELECT id, title, kind, import_order, source_name, revision FROM dictionaries "
            "ORDER BY import_order, id"
        )
        return [self._row_from(r) for r in rows]

    def resolve(self, titles: Sequence[str]) -> tuple[list[DictRow], list[str]]:
        """Map an ordered list of titles to their imported :class:`DictRow`s, preserving order.

        Returns ``(found, missing)`` — ``missing`` lists titles with no imported dictionary (the
        caller warns the user to run ``import``). Duplicate/unknown titles never raise."""
        by_title = {r.title: r for r in self.list_dictionaries()}
        found = [by_title[t] for t in titles if t in by_title]
        missing = [t for t in titles if t not in by_title]
        return found, missing

    def dict_counts(self, dict_id: int) -> dict[str, int]:
        """Row counts per table for one dictionary — for tests and doctor."""
        c = self._conn()
        return {
            t: c.execute(
                f"SELECT COUNT(*) FROM {t} WHERE dict_id=?",
                (dict_id,),
            ).fetchone()[0]
            for t in ("entries", "keys", "kanji", "term_meta", "tags")
        }
