"""One database, one client.

``dictionaries.sqlite`` used to be declared by two writers with two different schemas — whichever
opened the file first won, and the reader carried a duplicate of every entry query to cope. These pin
the merge: :mod:`saitenka_dict.schema` is the only declaration, and no entry point may diverge from it.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from saitenka_dict import DictionaryDatabase
from saitenka_dict.schema import SCHEMA_VERSION, ensure_schema

from saitenka.app import dictdb
from saitenka.app.config import DictDbOptions
from saitenka.app.dictdb import DictionaryDb

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


def test_both_entry_points_create_the_same_schema(tmp_path):
    """The regression itself: the app's handle and the package's admin API must agree, table for
    table and index for index. Before the merge they disagreed about every shared table."""
    DictionaryDb.open(tmp_path / "app.sqlite")
    DictionaryDatabase(tmp_path / "package.sqlite").initialize()

    assert _schema_of(tmp_path / "app.sqlite") == _schema_of(tmp_path / "package.sqlite")


def test_the_app_declares_no_dictionary_schema():
    """The app owns *policy* over this file — where it lives, how a reading connection is tuned — but
    not its shape. A CREATE here would be a second declaration drifting from the first again."""
    source = Path(dictdb.__file__).read_text(encoding="utf-8")

    assert "CREATE TABLE" not in source
    assert "CREATE INDEX" not in source


def test_the_app_writes_only_its_own_key_value_rows():
    """Every write to the dictionary tables goes through `saitenka-dict`. What the app retains is
    `meta_set`, which touches the key-value table it uses for its own bookkeeping and nothing else."""
    source = Path(dictdb.__file__).read_text(encoding="utf-8").upper()
    writes = [
        clause
        for clause in ("INSERT INTO", "INSERT OR REPLACE INTO", "UPDATE ", "DELETE FROM")
        if clause in source
    ]

    assert writes == ["INSERT OR REPLACE INTO"]
    assert source.count("INSERT OR REPLACE INTO META VALUES") == 1


def test_a_legacy_database_is_widened_in_place_not_rebuilt(tmp_path):
    """An existing user's DB predates the union columns. Opening it must ALTER them in, leaving the
    imported rows alone — an upgrade that forced a re-import would cost hours of dictionary builds."""
    path = tmp_path / "legacy.sqlite"
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

    entry_columns = {row[1] for row in db._conn().execute("PRAGMA table_info(entries)")}
    assert {"seq", "rules", "score", "term_tags"} <= entry_columns
    assert {"category", "notes", "score"} <= {
        row[1] for row in db._conn().execute("PRAGMA table_info(tags)")
    }
    assert db._conn().execute("SELECT term, seq, rules FROM entries").fetchone() == ("猫", None, "")


def test_an_import_keeps_the_yomitan_fields_the_app_used_to_discard(tmp_path):
    """`rules`/`score`/`term_tags` are real Yomitan data that drive deinflection display and result
    ordering. The app's writer parsed and dropped them, so the semantic store substituted blanks for
    every entry; one writer means they are simply stored."""
    db = DictionaryDb.open(tmp_path / "db.sqlite")
    row = db.import_zip(_dictionary_zip(tmp_path / "core.zip"), imported_at=AT)

    stored = (
        db._conn()
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

    stored = db._conn().execute("SELECT seq FROM entries WHERE dict_id=?", (row.id,)).fetchone()
    assert stored == (expected,)


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
        str(row[3]) for row in db._conn().execute(f"EXPLAIN QUERY PLAN {query}", params)
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
