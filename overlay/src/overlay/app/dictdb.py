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
import re
import sqlite3
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from overlay.app import paths
from overlay.app.bankreader import _title_of, classify_zip, read_json_bank
from overlay.app.config import DictDbOptions, resolve_dictdb

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

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
  dict_id INTEGER, id INTEGER, term TEXT, reading TEXT, glossary TEXT, tags TEXT, seq INTEGER,
  PRIMARY KEY(dict_id, id));
CREATE TABLE IF NOT EXISTS keys(dict_id INTEGER, key TEXT, id INTEGER);
CREATE TABLE IF NOT EXISTS kanji(
  dict_id INTEGER, chr TEXT, onyomi TEXT, kunyomi TEXT, tags TEXT, meanings TEXT, stats TEXT,
  PRIMARY KEY(dict_id, chr));
CREATE TABLE IF NOT EXISTS term_meta(
  dict_id INTEGER, term TEXT, mode TEXT, reading TEXT, rank INTEGER, disp TEXT, positions TEXT);
CREATE TABLE IF NOT EXISTS tags(dict_id INTEGER, code TEXT, name TEXT, ord INTEGER);
-- Persistent cache of the Anki known-word set (startup perf): one row per note, `words` = a JSON list
-- of the extracted forms, `mod` = the note's Anki modification time for the background subset-diff
-- refresh. Independent of dictionary imports (survives everything but a DB_SCHEMA rebuild → then a
-- one-time full reload repopulates it). See wordlists.KnownWords / reader_deps._load_known_words.
CREATE TABLE IF NOT EXISTS anki_known_cache(
  deck TEXT, note_id INTEGER, mod INTEGER, words TEXT, PRIMARY KEY(deck, note_id));
CREATE INDEX IF NOT EXISTS idx_keys ON keys(dict_id, key);
CREATE INDEX IF NOT EXISTS idx_meta_term ON term_meta(dict_id, term);
-- PitchSource.accents() (wordlists.py) queries `dict_id=? AND mode='pitch' AND (term=? OR reading=?)`.
-- idx_meta_term alone only covers the `term` branch, so SQLite falls back to a full per-dict_id scan
-- for the `reading` branch — confirmed via EXPLAIN QUERY PLAN + a py-spy profile showing this query as
-- ~28% of total sampled time under --stress (73k-row NHK pitch dict scanned per lookup, up to 3x per
-- tooltip). This second index lets SQLite's OR-optimization split the query into two indexed seeks.
CREATE INDEX IF NOT EXISTS idx_meta_reading ON term_meta(dict_id, mode, reading);
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


@dataclass(frozen=True)
class DictStat:
    """One imported dictionary's content-free inventory: its :class:`DictRow` plus per-table row
    counts. A ``dict``-kind dictionary with ``entries`` but ``tags == 0`` is the tell of a
    sidecar-era import that predates tags-in-sqlite — re-import to populate the tag pills."""

    row: DictRow
    imported_at: str
    schema_version: int
    counts: dict[str, int]  # entries / keys / kanji / term_meta / tags


@dataclass(frozen=True)
class DbStats:
    """A content-free snapshot of the consolidated DB — safe to put in a diagnostics bundle: it names
    no term, reading, or gloss, only the schema, file size, and per-dictionary row counts."""

    path: Path
    exists: bool
    size_bytes: int
    schema: int | None
    dicts: list[DictStat]


def _revision_of(zf: zipfile.ZipFile) -> str:
    try:
        return str(json.loads(zf.read("index.json")).get("revision", "") or "")
    except Exception:
        log.debug("index.json revision read failed", exc_info=True)
        return ""


def _is_occurrence_based(zf: zipfile.ZipFile) -> bool:
    """True when index.json declares ``frequencyMode: "occurrence-based"`` — the freq *value* is then an
    occurrence COUNT (higher = more frequent), not a rank. Left untouched, the banded scorer (which
    assumes rank semantics: 1 = most frequent) would colour such a dict inverted, so the count is
    converted to a rank at import — see :func:`_apply_occurrence_ranks`. Yomitan's default is
    ``rank-based``."""
    try:
        return json.loads(zf.read("index.json")).get("frequencyMode") == "occurrence-based"
    except Exception:
        log.debug("index.json frequencyMode read failed", exc_info=True)
        return False


_LEADING_INT = re.compile(r"\s*(-?\d+)")


def _coerce_rank(v) -> int | None:
    """A freq value → int rank, tolerating the shapes seen in the wild. A *string* value takes the
    LEADING integer (``"118,121"`` → ``118``), never the comma-stripped concatenation ``118121`` — a
    grouped ``"rank, occurrences"`` display means the rank is the first number (the bug SubMiner fixed).
    ``bool`` is rejected (``True`` is not rank 1); non-numeric → ``None`` (no colour, never a crash)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and (m := _LEADING_INT.match(v)):
        return int(m.group(1))
    return None


def _parse_freq_entry(
    data,
) -> tuple[str | None, int | None, str | None]:
    """``(reading, rank, disp)`` from a freq term_meta value — plain int, ``{"value"/
    "displayValue"}``, ``{"reading","frequency"}``, or the JLPT
    ``{"frequency": {"value": -1, "displayValue": "N5"}}`` form. ``rank`` is always an ``int``/``None``
    (never a raw string) so the coloring hot path can compare it — see :func:`_coerce_rank`."""
    reading, rank, disp = None, None, None
    if isinstance(data, (int, float, str)):
        rank = _coerce_rank(data)
    elif isinstance(data, dict):
        reading = data.get("reading")
        fval = data.get("frequency", data)
        if isinstance(fval, dict):
            rank = _coerce_rank(fval.get("value"))
            disp = fval.get("displayValue")
        else:
            rank = _coerce_rank(fval)
    return reading, rank, disp


def _parse_pitch_entry(term: str, data) -> tuple[str, str] | None:
    """``(reading, positions_json)`` from a pitch term_meta value (``{"reading", "pitches":
    [{"position": n}]}``), or ``None`` when there's no usable pitch position."""
    if not isinstance(data, dict):
        return None
    reading = data.get("reading") or term
    positions = [
        pos
        for p in data.get("pitches", [])
        if isinstance(p, dict) and isinstance(pos := p.get("position"), int)
    ]
    if not positions:
        return None
    return reading, json.dumps(positions)


def _read_term_meta(
    zf: zipfile.ZipFile,
) -> Iterable[tuple[str, str, str | None, int | None, str | None, str | None]]:
    """Yield ``(term, mode, reading, rank, disp, positions_json)`` over every freq/pitch term_meta entry.

    Covers the freq value-shapes (plain int, ``{"value"/"displayValue"}``, ``{"reading","frequency"}``,
    and the JLPT ``{"frequency": {"value": -1, "displayValue": "N5"}}`` form) and pitch entries
    (``{"reading", "pitches": [{"position": n}]}``) — the same shapes ``wordlists`` parses at read time.
    """
    for name in sorted(zf.namelist()):
        if not (name.startswith("term_meta_bank") and name.endswith(".json")):
            continue
        for entry in read_json_bank(zf, name) or []:
            if len(entry) < 3 or not isinstance(entry[1], str):
                continue
            term, mode, data = entry[0], entry[1], entry[2]
            if mode == "freq":
                reading, rank, disp = _parse_freq_entry(data)
                yield term, "freq", reading, rank, disp, None
            elif mode == "pitch":
                parsed = _parse_pitch_entry(term, data)
                if parsed is None:
                    continue
                reading, positions_json = parsed
                yield term, "pitch", reading, None, None, positions_json


def _apply_occurrence_ranks(
    rows: list[tuple[str, str, str | None, int | None, str | None, str | None]],
) -> list[tuple[str, str, str | None, int | None, str | None, str | None]]:
    """Convert occurrence COUNTS to 1-based dense ranks (most-frequent = 1) for an occurrence-based freq
    dict, so the banded scorer — which assumes rank semantics — colours it correctly instead of
    inverted. The original count is preserved in ``disp`` (when the dict gave no explicit displayValue),
    so the tooltip still shows the real frequency, not the derived rank. Pitch rows and non-positive
    ranks (e.g. the JLPT ``-1`` sentinel) pass through untouched."""
    counts = sorted(
        {r[3] for r in rows if r[1] == "freq" and isinstance(r[3], int) and r[3] > 0}, reverse=True
    )
    rank_of = {c: i + 1 for i, c in enumerate(counts)}
    out: list[tuple[str, str, str | None, int | None, str | None, str | None]] = []
    for term, mode, reading, rank, disp, positions in rows:
        if mode == "freq" and isinstance(rank, int) and rank > 0:
            out.append(
                (
                    term,
                    mode,
                    reading,
                    rank_of[rank],
                    disp if disp is not None else str(rank),
                    positions,
                )
            )
        else:
            out.append((term, mode, reading, rank, disp, positions))
    return out


def _extract_tags(zf: zipfile.ZipFile) -> list[tuple[str, str, int]]:
    """Yomitan ``tag_bank_*.json`` → [(code, display_name, order)] for defTag pills (★ / priority form)."""
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

    def __init__(self, path: str | Path, db_opts: DictDbOptions | None = None):
        self.path = Path(path)
        self._local = threading.local()
        self._opts = db_opts if db_opts is not None else resolve_dictdb()

    # --- lifecycle ----------------------------------------------------------------------------

    @classmethod
    def open(
        cls, path: str | Path | None = None, db_opts: DictDbOptions | None = None
    ) -> DictionaryDb:
        """Open (creating + schema-initialising if needed) the consolidated DB."""
        db = cls(path or db_path(), db_opts)
        db.path.parent.mkdir(parents=True, exist_ok=True)
        db._ensure_schema()
        return db

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")  # persists in the DB header; enables ro readers
            conn.executescript(_SCHEMA_SQL)
            # Additive migration for a DB created before `entries.seq` existed (#255): CREATE TABLE IF
            # NOT EXISTS above never touches an existing table's columns, so a pre-#255 DB needs this
            # explicit ALTER. Safe to re-run (guarded by table_info) and doesn't disturb existing rows.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
            if "seq" not in cols:
                conn.execute("ALTER TABLE entries ADD COLUMN seq INTEGER")
            row = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
            if row is None:
                conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(DB_SCHEMA),))
            # One-time catch-up for DBs imported before idx_meta_reading existed: SQLite's query
            # planner needs sqlite_stat1 (via ANALYZE) to pick the right index for term_meta's OR
            # query (PitchSource.accents) — without it, it may scan instead of seek even with the
            # index present. `import_zip` keeps stats fresh for new imports; this catches installs
            # that already have data. Runs once (meta flag), not on every open — ANALYZE cost scales
            # with table size (~1.3s on a 2.3M-row term_meta).
            if conn.execute("SELECT v FROM meta WHERE k='analyzed'").fetchone() is None:
                conn.execute("ANALYZE term_meta")
                conn.execute("INSERT OR REPLACE INTO meta VALUES('analyzed', '1')")
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        """A per-thread read-only connection (mmap'd, roomy page cache) for lookups."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            # mmap a MODEST window of the DB so cold lookups hit page-cache-backed memory instead of
            # pread syscalls. Kept small (256 MiB, was 1 GiB) because the DB is multi-GB and this is
            # set on EACH per-thread connection (main + prefetch workers); on Windows the mapped view
            # counts toward the process working set, so a 1 GiB window × N threads was inflating RAM by
            # gigabytes. A benchmark showed the mmap win over pread was mostly a page-cache artifact, so
            # shrinking the window costs ~nothing. 32 MiB page cache per connection (negative = KiB) —
            # both tunable via ``[dictdb]`` in overlay.toml (app/config.py: DictDbOptions).
            c.execute(f"PRAGMA mmap_size={self._opts.mmap_size}")
            c.execute(f"PRAGMA cache_size=-{self._opts.cache_size_kib}")
            self._local.conn = c
        return c

    # --- Anki known-word cache (startup perf; see wordlists / reader_deps) --------------------

    def known_cache_read(
        self, decks: Sequence[str]
    ) -> dict[str, dict[int, tuple[int, list[list[str]]]]]:
        """``{deck: {note_id: (mod, [[surface, reading]])}}`` from the persistent cache — the last-known
        extracted forms per note, for an instant startup load. A deck with no cached rows maps to ``{}``.
        The payload shape is opaque here (JSON round-trip); :mod:`wordlists` owns its meaning + format
        version."""
        out: dict[str, dict[int, tuple[int, list[list[str]]]]] = {d: {} for d in decks}
        if not decks:
            return out
        placeholders = ",".join("?" * len(decks))
        for deck, note_id, mod, words in self._conn().execute(
            f"SELECT deck, note_id, mod, words FROM anki_known_cache WHERE deck IN ({placeholders})",  # noqa: S608  # placeholders are '?'s, params bound below
            tuple(decks),
        ):
            out.setdefault(deck, {})[note_id] = (mod, json.loads(words))
        return out

    def known_cache_write(
        self,
        deck: str,
        upserts: Sequence[tuple[int, int, list[list[str]]]],
        deleted_ids: Sequence[int] = (),
    ) -> None:
        """Apply one deck's diff (``upserts`` = ``(note_id, mod, [[surface, reading]])``; ``deleted_ids`` gone from
        Anki) in a single transaction. Runs on the background refresh thread — a fresh RW connection
        keeps it off the per-thread RO lookup conns; ``timeout`` rides out a concurrent lookup's lock."""
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            if deleted_ids:
                conn.executemany(
                    "DELETE FROM anki_known_cache WHERE deck=? AND note_id=?",
                    [(deck, nid) for nid in deleted_ids],
                )
            if upserts:
                conn.executemany(
                    "INSERT OR REPLACE INTO anki_known_cache(deck, note_id, mod, words) "
                    "VALUES(?,?,?,?)",
                    [
                        (deck, nid, mod, json.dumps(words, ensure_ascii=False))
                        for nid, mod, words in upserts
                    ],
                )
            conn.commit()
        finally:
            conn.close()

    def meta_get(self, key: str) -> str | None:
        row = self._conn().execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, value))
            conn.commit()
        finally:
            conn.close()

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
                    occ = _is_occurrence_based(zf)
                    self._load_meta_banks(conn, zf, did, on_bank, occurrence_based=occ)
                    if kind == "freq":
                        # Persist the ORIGINAL frequency mode so the harmonic-blend pill can exclude
                        # occurrence-based dicts (their converted rank is a per-corpus dense rank, not
                        # comparable to a real rank-based list — only ranks may be blended). Same
                        # transaction; unknown key on old imports defaults to rank-based downstream.
                        conn.execute(
                            "INSERT OR REPLACE INTO meta VALUES(?, ?)",
                            (f"freqmode:{did}", "occurrence" if occ else "rank"),
                        )
            if kind != "dict":
                # Keep term_meta's query-planner stats fresh after every freq/pitch import — see
                # _ensure_schema's one-time catch-up for the reasoning (PitchSource.accents needs
                # ANALYZE to pick idx_meta_reading/idx_meta_term over a full scan).
                conn.execute("ANALYZE term_meta")
                conn.commit()
            row = self._row_by_id(conn, did)
        finally:
            conn.close()
        return row

    def _load_term_bank(
        self, conn: sqlite3.Connection, zf: zipfile.ZipFile, name: str, did: int, rid: int
    ) -> int:
        bank = read_json_bank(zf, name)  # tolerant of wrong-CRC Yomitan zips (data intact)
        if bank is None:
            return rid
        persist_seq = self._opts.persist_seq
        rows, keys = [], []
        for e in bank:  # [term, reading, defTags, rules, score, glossary[], seq, termTags]
            rid += 1
            term, reading = e[0], e[1] or e[0]
            seq = e[6] if persist_seq and len(e) > 6 and isinstance(e[6], int) else None
            rows.append((did, rid, term, reading, json.dumps(e[5], ensure_ascii=False), e[2], seq))
            keys.append((did, term, rid))
            if reading != term:
                keys.append((did, reading, rid))
        conn.executemany("INSERT INTO entries VALUES(?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT INTO keys VALUES(?,?,?)", keys)
        return rid

    def _load_kanji_bank(
        self, conn: sqlite3.Connection, zf: zipfile.ZipFile, name: str, did: int
    ) -> None:
        bank = read_json_bank(zf, name)  # [char, onyomi, kunyomi, tags, meanings[], stats{}]
        if bank is None:
            return
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

    def _load_dict_banks(
        self,
        conn: sqlite3.Connection,
        zf: zipfile.ZipFile,
        did: int,
        on_bank: Callable[[int, int], None] | None,
    ) -> None:
        names = sorted(zf.namelist())
        term_banks = [n for n in names if n.startswith("term_bank") and n.endswith(".json")]
        kanji_banks = [n for n in names if n.startswith("kanji_bank") and n.endswith(".json")]
        total = len(term_banks) + len(kanji_banks)
        done = 0
        rid = 0
        for name in term_banks:
            rid = self._load_term_bank(conn, zf, name, did, rid)
            done += 1
            if on_bank:
                on_bank(done, total)
        for name in kanji_banks:
            self._load_kanji_bank(conn, zf, name, did)
            done += 1
            if on_bank:
                on_bank(done, total)
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
        *,
        occurrence_based: bool = False,
    ) -> None:
        if on_bank:
            on_bank(0, 1)
        rows = list(_read_term_meta(zf))
        if occurrence_based:
            rows = _apply_occurrence_ranks(rows)
        conn.executemany(
            "INSERT INTO term_meta VALUES(?,?,?,?,?,?,?)",
            [(did, *rest) for rest in rows],
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
            conn.execute(
                f"DELETE FROM {table} WHERE dict_id=?",  # noqa: S608  # table name is an internal constant; the value is parameterized with ?
                (did,),
            )
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
                f"SELECT COUNT(*) FROM {t} WHERE dict_id=?",  # noqa: S608  # table name is an internal constant; the value is parameterized with ?
                (dict_id,),
            ).fetchone()[0]
            for t in ("entries", "keys", "kanji", "term_meta", "tags")
        }

    def stats(self) -> DbStats:
        """Content-free inventory (schema, file size, per-dictionary row counts) for report/doctor —
        no term/reading/gloss text, so it's safe in a shared diagnostics bundle. One grouped read of
        the ``dictionaries`` table plus :meth:`dict_counts` per dict (a handful, off any hot path)."""
        if not self.path.exists():
            return DbStats(self.path, exists=False, size_bytes=0, schema=None, dicts=[])
        raw = self.meta_get("schema")
        meta = {
            int(r[0]): (r[1] or "", int(r[2] or 0))
            for r in self._conn().execute(
                "SELECT id, imported_at, schema_version FROM dictionaries"
            )
        }
        dicts = [
            DictStat(
                row,
                meta.get(row.id, ("", 0))[0],
                meta.get(row.id, ("", 0))[1],
                self.dict_counts(row.id),
            )
            for row in self.list_dictionaries()
        ]
        return DbStats(
            self.path,
            exists=True,
            size_bytes=self.path.stat().st_size,
            schema=int(raw) if raw is not None and raw.isdigit() else None,
            dicts=dicts,
        )
