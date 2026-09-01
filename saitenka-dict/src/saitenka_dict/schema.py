"""The consolidated dictionary database's schema — declared once, for every client.

This file is the single owner of what ``dictionaries.sqlite`` looks like. It exists because the schema
used to be declared twice (an app-side writer and this package's), and the two disagreed about every
shared table: whichever opened the file first won, and the reader carried a duplicate of each query to
cope. :func:`ensure_schema` is the only sanctioned way to create or upgrade the file.

The layout is additive across versions: a DB written by an older release is brought forward with
``ALTER``/``CREATE INDEX`` rather than a rebuild, so an upgrade never forces a re-import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

SCHEMA_VERSION = 2

_TABLES = """
CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS dictionaries(
  id INTEGER PRIMARY KEY, title TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
  import_order INTEGER NOT NULL, source_name TEXT NOT NULL, revision TEXT NOT NULL,
  imported_at TEXT NOT NULL, schema_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS entries(
  dict_id INTEGER NOT NULL, id INTEGER NOT NULL, term TEXT NOT NULL, reading TEXT NOT NULL,
  glossary TEXT NOT NULL, tags TEXT NOT NULL, seq INTEGER,
  rules TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL DEFAULT 0,
  term_tags TEXT NOT NULL DEFAULT '', PRIMARY KEY(dict_id, id));
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
  category TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
  score INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS media(
  dict_id INTEGER NOT NULL, path TEXT NOT NULL, png BLOB NOT NULL, PRIMARY KEY(dict_id, path));
"""

#: ``idx_keys`` leads with ``key`` because the semantic store looks a form up across every dictionary
#: at once (no ``dict_id`` to seek on). Every plan is pinned by an EXPLAIN QUERY PLAN test, on a
#: migrated database as well as a fresh one — see :func:`_reconcile_indexes` for why that matters.
#:
#: ``idx_meta_reading``: pitch lookup matches ``term=? OR reading=?`` and ``idx_term_meta_term``
#: covers only the ``term`` branch, so without this SQLite falls back to a full per-``dict_id`` scan
#: for ``reading`` — EXPLAIN QUERY PLAN plus a py-spy profile put that query at ~28% of total sampled
#: time under ``--stress``. With both, the OR-optimization splits it into two indexed seeks.
_INDEXES = {
    "idx_keys": "CREATE INDEX idx_keys ON keys(key, dict_id)",
    "idx_entries_seq": "CREATE INDEX idx_entries_seq ON entries(seq, dict_id)",
    "idx_kanji_char": "CREATE INDEX idx_kanji_char ON kanji(chr, dict_id)",
    "idx_term_meta_term": "CREATE INDEX idx_term_meta_term ON term_meta(dict_id, term)",
    "idx_meta_reading": "CREATE INDEX idx_meta_reading ON term_meta(dict_id, mode, reading)",
}

#: Indexes an older release created that a current one supersedes. ``idx_meta_term`` covered exactly
#: what ``idx_term_meta_term`` covers, so leaving it costs a second copy of a multi-million-row index
#: and a write on every insert, for nothing.
_RETIRED_INDEXES = ("idx_meta_term",)

#: ``(PRAGMA, ((column, ALTER), ...))`` — additive column upgrades for a DB created by an older
#: release. ``CREATE TABLE IF NOT EXISTS`` never touches an existing table's columns, so each widening
#: needs an explicit guarded ALTER. Re-running is a no-op.
_MIGRATIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
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
            ("revision", "ALTER TABLE dictionaries ADD COLUMN revision TEXT NOT NULL DEFAULT ''"),
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
            ("seq", "ALTER TABLE entries ADD COLUMN seq INTEGER"),
            ("rules", "ALTER TABLE entries ADD COLUMN rules TEXT NOT NULL DEFAULT ''"),
            ("score", "ALTER TABLE entries ADD COLUMN score INTEGER NOT NULL DEFAULT 0"),
            ("term_tags", "ALTER TABLE entries ADD COLUMN term_tags TEXT NOT NULL DEFAULT ''"),
        ),
    ),
    (
        "PRAGMA table_info(tags)",
        (
            ("category", "ALTER TABLE tags ADD COLUMN category TEXT NOT NULL DEFAULT ''"),
            ("notes", "ALTER TABLE tags ADD COLUMN notes TEXT NOT NULL DEFAULT ''"),
            ("score", "ALTER TABLE tags ADD COLUMN score INTEGER NOT NULL DEFAULT 0"),
        ),
    ),
)

#: Bumped whenever the index set changes, so a DB carrying stale ``sqlite_stat1`` re-ANALYZEs once.
#: Without fresh stats SQLite's planner can keep scanning ``term_meta`` even with the index present.
_ANALYZE_GENERATION = "2"

#: Generous, because the thing it waits on is a one-time index rebuild on a multi-GB file, and the
#: alternative is a second mpv instance failing to start during someone's first post-upgrade launch.
_BUSY_TIMEOUT_MS = 120_000


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create or bring forward the schema on an open read/write connection. Idempotent.

    WAL is set here rather than by a caller: it is a property of the *file* (it persists in the header
    and is what lets several mpv instances read while an import writes), so every client that can
    create the DB must agree on it.
    """
    # Several mpv instances open this file, and an upgrade that rebuilds the largest index holds the
    # write lock for as long as that takes. Without a busy timeout the default is zero: a second
    # process starting during the upgrade fails outright rather than waiting for it.
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(_TABLES)
    for pragma, migrations in _MIGRATIONS:
        present = {row[1] for row in connection.execute(pragma)}
        for column, statement in migrations:
            if column not in present:
                connection.execute(statement)
    rebuilt = _reconcile_indexes(connection)
    if connection.execute("SELECT COUNT(*) FROM schema_info").fetchone()[0] == 0:
        connection.execute("INSERT INTO schema_info VALUES (?)", (SCHEMA_VERSION,))
    else:
        connection.execute("UPDATE schema_info SET version=?", (SCHEMA_VERSION,))
    connection.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
    _analyze_once(connection, force=rebuilt)
    connection.commit()


def _reconcile_indexes(connection: sqlite3.Connection) -> bool:
    """Bring the index set to :data:`_INDEXES`, rebuilding any that exists under a different
    definition. Returns whether anything changed.

    ``CREATE INDEX IF NOT EXISTS`` matches on the **name** alone — it will not replace an index whose
    columns differ, and reports no error. So a release that reorders an index reaches only databases
    created after it: every existing install silently keeps the old one, which is how the reorder
    ``idx_keys`` needs (``key`` leading, for the store's cross-dictionary lookup) would otherwise have
    shipped to nobody. Dropping and recreating costs one rebuild, once.
    """
    existing = dict(
        connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    )
    stale = [name for name in _RETIRED_INDEXES if name in existing]
    rebuild = {
        name: statement
        for name, statement in _INDEXES.items()
        if (current := existing.get(name)) is None or " ".join(current.split()) != statement
    }
    if not stale and not rebuild:
        return False
    # One transaction: `DROP INDEX` autocommits on its own, so an upgrade interrupted between the drop
    # and the create (lock, disk full, Ctrl-C) would otherwise leave the hottest index simply missing.
    with connection:
        for name in (*stale, *(name for name in rebuild if name in existing)):
            connection.execute(f"DROP INDEX {name}")  # an internal constant, not input
        for statement in rebuild.values():
            connection.execute(statement)
    return True


def _analyze_once(connection: sqlite3.Connection, *, force: bool = False) -> None:
    """Refresh ``term_meta``'s planner statistics after an index-set change, once per generation.

    ANALYZE costs ~1.3s on a 2.3M-row ``term_meta``, so it must not run on every open — but skipping
    it entirely leaves an existing install on the plan it had before the index existed. ``force``
    after a rebuild: the stored stats name indexes that no longer exist in that shape.
    """
    row = connection.execute("SELECT v FROM meta WHERE k='analyzed'").fetchone()
    if not force and row is not None and row[0] == _ANALYZE_GENERATION:
        return
    connection.execute("ANALYZE term_meta")
    connection.execute("INSERT OR REPLACE INTO meta VALUES('analyzed', ?)", (_ANALYZE_GENERATION,))
