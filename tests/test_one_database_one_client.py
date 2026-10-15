"""Dictionary schema convergence and released-database migration.

``dictionaries.sqlite`` used to be declared by two writers with two different schemas — whichever
opened the file first won. These pin fresh-schema convergence and released-database migration behavior.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from typing import TYPE_CHECKING

import pytest
from saitenka_dict import DictionaryDatabase
from saitenka_dict.schema import SCHEMA_VERSION, ensure_schema
from saitenka_tokenize.japanese import Token

from saitenka.app.config import DictDbOptions
from saitenka.app.dictdb import DictionaryDb
from saitenka.app.dictionary import DictionarySet

if TYPE_CHECKING:
    from pathlib import Path

AT = "2026-08-31T00:00:00"


def _schema_of(path: Path) -> set[tuple[str, str, str]]:
    connection = sqlite3.connect(path)
    try:
        return {
            (kind, name, " ".join(sql.split()))
            for kind, name, sql in connection.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }
    finally:
        connection.close()


def _dictionary_zip(path: Path, title: str = "Core") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": title, "format": 3, "revision": "r1"}))
        archive.writestr(
            "term_bank_1.json",
            json.dumps([["猫", "ねこ", "n", "v5", 7, ["cat"], 101, "common"]], ensure_ascii=False),
        )
    return path


# The app's per-(dict, form) point lookup and its batched multi-dict form.
_APP_POINT_LOOKUP = (
    "SELECT e.id FROM keys k JOIN entries e ON k.dict_id = e.dict_id AND k.id = e.id "
    "WHERE k.dict_id = ? AND k.key = ?"
)
_APP_BATCH_LOOKUP = (
    "SELECT k.dict_id, e.id FROM keys k JOIN entries e ON k.dict_id = e.dict_id "
    "AND k.id = e.id WHERE k.dict_id IN (?, ?) AND k.key IN (?, ?)"
)
# The semantic store's key-only lookup — the one a `(dict_id, key)` index cannot seek, and the
# reason the union index leads with `key`.
_STORE_LOOKUP = (
    "SELECT e.id FROM keys k JOIN entries e ON e.dict_id=k.dict_id AND e.id=k.id "
    "WHERE k.key IN (SELECT value FROM json_each(?))"
)


def test_both_entry_points_create_the_same_schema(tmp_path):
    """The regression itself: the app's handle and the package's admin API must agree, table for
    table and index for index. Before the merge they disagreed about every shared table.

    Two FRESH databases. A migrated one converges on indexes (pinned separately) but keeps its
    original nullability on columns that already existed — `CREATE TABLE IF NOT EXISTS` never rewrites
    a table, and no consumer depends on the difference.
    """
    DictionaryDb.open(tmp_path / "app.sqlite")
    DictionaryDatabase(tmp_path / "package.sqlite").initialize()

    assert _schema_of(tmp_path / "app.sqlite") == _schema_of(tmp_path / "package.sqlite")


#: The shape a released version of the app actually created. A migration test that invents its own
#: "old" schema tests the migration against fiction; this is copied from the retired `_SCHEMA_SQL`.
_SHIPPED_LEGACY_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS tags(
  dict_id INTEGER, code TEXT, name TEXT, ord INTEGER, category TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS media(dict_id INTEGER, path TEXT, png BLOB, PRIMARY KEY(dict_id, path));
CREATE INDEX IF NOT EXISTS idx_keys ON keys(dict_id, key);
CREATE INDEX IF NOT EXISTS idx_meta_term ON term_meta(dict_id, term);
CREATE INDEX IF NOT EXISTS idx_meta_reading ON term_meta(dict_id, mode, reading);
"""


def _shipped_legacy_db(path: Path) -> Path:
    """A database as a released version left it: old schema, one imported dictionary, real rows."""
    connection = sqlite3.connect(path)
    connection.executescript(_SHIPPED_LEGACY_SCHEMA)
    connection.execute(
        "INSERT INTO dictionaries VALUES(1,'JMdict','dict',0,'jmdict.zip','r1','2026-01-01',1)"
    )
    connection.execute("INSERT INTO entries VALUES(1,1,'猫','ねこ','[\"cat\"]','n',101)")
    connection.executemany("INSERT INTO keys VALUES(?,?,?)", [(1, "猫", 1), (1, "ねこ", 1)])
    connection.execute("INSERT INTO term_meta VALUES(1,'猫','freq','ねこ',42,'42',NULL)")
    connection.execute("INSERT INTO tags VALUES(1,'n','n',1,'partOfSpeech','noun')")
    connection.executemany(
        "INSERT OR REPLACE INTO meta VALUES(?,?)",
        [("schema", "1"), ("freqmode:1", "rank"), ("analyzed", "1")],
    )
    connection.commit()
    connection.close()
    return path


def test_an_upgrade_rebuilds_an_index_whose_columns_changed(tmp_path):
    """`CREATE INDEX IF NOT EXISTS` matches on the NAME alone: it will not replace an index whose
    columns differ, and reports no error.

    So an index reorder reaches only databases created after it, and every existing install silently
    keeps the old one — which for `idx_keys` means the store's cross-dictionary lookup goes on
    scanning the whole `keys` table. Pinned on a MIGRATED database, because a fresh one cannot show
    this at all.
    """
    db = DictionaryDb.open(_shipped_legacy_db(tmp_path / "legacy.sqlite"))

    definitions = {
        name: " ".join(sql.split())
        for name, sql in db.connection().execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }
    assert definitions["idx_keys"] == "CREATE INDEX idx_keys ON keys(key, dict_id)"
    # Superseded by idx_term_meta_term, which covers the same columns — two copies of a
    # multi-million-row index cost a write on every insert for nothing.
    assert "idx_meta_term" not in definitions

    plan = " ".join(
        str(row[3])
        for row in db.connection().execute(
            f"EXPLAIN QUERY PLAN {_STORE_LOOKUP}", (json.dumps(["猫"]),)
        )
    )
    assert "SEARCH k USING INDEX idx_keys" in plan
    assert "SCAN k" not in plan


def test_a_migrated_database_keeps_every_row_and_still_answers(tmp_path):
    """The rows are the expensive part — a multi-GB import is hours of work. An upgrade may add
    columns and rebuild indexes, but must not lose a row or break the lookup that reads them."""
    path = _shipped_legacy_db(tmp_path / "legacy.sqlite")
    db = DictionaryDb.open(path)

    assert db.connection().execute(
        "SELECT dict_id, id, term, reading, glossary, tags, seq FROM entries"
    ).fetchall() == [(1, 1, "猫", "ねこ", '["cat"]', "n", 101)]
    # The one fact nothing downstream can reconstruct once the counts have been ranked.
    assert db.meta_get("freqmode:1") == "rank"

    entry = DictionarySet.from_db(db, ["JMdict"]).entry_for(
        Token(surface="猫", lemma="猫", reading="ねこ", pos="名詞", start=0, end=1)
    )
    assert [(d.dict_name, d.content) for d in entry.defs] == [("JMdict", ["cat"])]


def test_a_legacy_database_is_widened_in_place_not_rebuilt(tmp_path):
    """An existing user's DB predates the union columns. Opening it must ALTER them in, leaving the
    imported rows alone — an upgrade that forced a re-import would cost hours of dictionary builds."""
    path = tmp_path / "narrow.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE entries(dict_id INTEGER, id INTEGER, term TEXT, reading TEXT, "
        "glossary TEXT, tags TEXT, PRIMARY KEY(dict_id, id));"
        "CREATE TABLE tags(dict_id INTEGER, code TEXT, name TEXT, ord INTEGER);"
        "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);"
    )
    connection.execute("INSERT INTO entries VALUES(1, 1, '猫', 'ねこ', '[\"cat\"]', '')")
    connection.commit()
    connection.close()

    db = DictionaryDb.open(path)

    entry_columns = {row[1] for row in db.connection().execute("PRAGMA table_info(entries)")}
    assert {"seq", "rules", "score", "term_tags"} <= entry_columns
    assert {"category", "notes", "score"} <= {
        row[1] for row in db.connection().execute("PRAGMA table_info(tags)")
    }
    assert db.connection().execute("SELECT term, seq, rules FROM entries").fetchone() == (
        "猫",
        None,
        "",
    )


def test_an_import_keeps_the_yomitan_fields_the_app_used_to_discard(tmp_path):
    """`rules`/`score`/`term_tags` are real Yomitan data that drive deinflection display and result
    ordering. The app's writer parsed and dropped them, so the semantic store substituted blanks for
    every entry; one writer means they are simply stored."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(_dictionary_zip(tmp_path / "core.zip"), imported_at=AT)

    stored = (
        db.connection()
        .execute("SELECT rules, score, term_tags FROM entries WHERE dict_id=?", (row.id,))
        .fetchone()
    )
    assert stored == ("v5", 7, "common")


@pytest.mark.parametrize(("persist_seq", "expected"), [(False, None), (True, 101)])
def test_the_seq_column_still_follows_the_app_option(tmp_path, persist_seq, expected):
    """`[dictdb] persist_seq` is opt-in and off by default; routing the write through the package must
    not quietly start storing a column the deployment declined."""
    db = DictionaryDb.open(tmp_path / "db.sqlite", DictDbOptions(persist_seq=persist_seq))
    row = db.import_zip(_dictionary_zip(tmp_path / "core.zip"), imported_at=AT)

    stored = (
        db.connection().execute("SELECT seq FROM entries WHERE dict_id=?", (row.id,)).fetchone()
    )
    assert stored == (expected,)


@pytest.mark.parametrize(
    ("query", "params"),
    [
        (_APP_POINT_LOOKUP, (1, "猫")),
        (_APP_BATCH_LOOKUP, (1, 2, "猫", "ねこ")),
        (_STORE_LOOKUP, (json.dumps(["猫"]),)),
    ],
)
def test_every_key_lookup_seeks_the_index_rather_than_scanning(tmp_path, query, params):
    """`idx_keys` leads with `key` so the store's cross-dictionary lookup can seek. That reordering is
    reasoned from query shape rather than measured, so the app's own lookups are pinned here: a plan
    naming SCAN instead of SEARCH means the reorder cost the hot path its index."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    db.import_zip(_dictionary_zip(tmp_path / "core.zip"), imported_at=AT)

    plan = " ".join(
        str(row[3]) for row in db.connection().execute(f"EXPLAIN QUERY PLAN {query}", params)
    )

    assert "SEARCH k USING INDEX idx_keys" in plan
    assert "SCAN k" not in plan


def test_the_schema_version_is_one_number(tmp_path):
    """The version the file records and the version an imported dictionary is stamped with come from
    the same constant — they used to be a package `2` and an app `1` written into one table."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    db.import_zip(_dictionary_zip(tmp_path / "core.zip"), imported_at=AT)

    assert db.stats().schema == SCHEMA_VERSION
    assert [d.schema_version for d in db.stats().dicts] == [SCHEMA_VERSION]


def test_ensure_schema_is_idempotent(tmp_path):
    """It runs on every open, so a second pass must be a no-op rather than an accumulating ALTER."""
    path = tmp_path / "db.sqlite"
    connection = sqlite3.connect(path)
    try:
        ensure_schema(connection)
        first = _schema_of(path)
        ensure_schema(connection)
    finally:
        connection.close()

    assert _schema_of(path) == first
