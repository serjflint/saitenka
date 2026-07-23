"""Streaming Yomitan database (dexie) import → reconstructed Yomitan .zip dictionaries."""

from __future__ import annotations

import json

import pytest

from overlay.app import yomitan_db_import as ydb


def _dexie(tmp_path):
    """A tiny but structurally faithful dexie export: 1 def dict + a freq dict (via termMeta)."""
    data = {
        "formatName": "dexie",
        "formatVersion": 1,
        "data": {
            "databaseName": "dict",
            "databaseVersion": 6,
            "tables": [
                {"name": "dictionaries", "rowCount": 1},
                {"name": "terms", "rowCount": 2},
                {"name": "termMeta", "rowCount": 1},
                {"name": "tagMeta", "rowCount": 1},
            ],
            "data": [
                {
                    "tableName": "dictionaries",
                    "inbound": False,
                    "rows": [
                        {"$": [1, {"title": "TestDict", "revision": "r1", "sequenced": True}]}
                    ],
                },
                {
                    "tableName": "terms",
                    "inbound": True,
                    "rows": [
                        {
                            "expression": "猫",
                            "reading": "ねこ",
                            "definitionTags": "n",
                            "rules": "",
                            "score": 0,
                            "glossary": ["cat"],
                            "sequence": 1,
                            "termTags": "",
                            "dictionary": "TestDict",
                        },
                        {
                            "expression": "犬",
                            "reading": "いぬ",
                            "definitionTags": "n",
                            "rules": "",
                            "score": 0,
                            "glossary": ["dog"],
                            "sequence": 2,
                            "termTags": "",
                            "dictionary": "TestDict",
                        },
                    ],
                },
                {
                    "tableName": "termMeta",
                    "inbound": False,
                    "rows": [
                        {
                            "$": [
                                1,
                                {
                                    "expression": "猫",
                                    "mode": "freq",
                                    "data": {"reading": "ねこ", "frequency": 100},
                                    "dictionary": "TestFreq",
                                },
                            ],
                            "$types": {"$": {"": "arrayNonindexKeys"}},
                        }
                    ],
                },
                {
                    "tableName": "tagMeta",
                    "inbound": False,
                    "rows": [
                        {
                            "$": [
                                1,
                                {
                                    "name": "n",
                                    "category": "partOfSpeech",
                                    "order": 0,
                                    "notes": "noun",
                                    "score": 0,
                                    "dictionary": "TestDict",
                                },
                            ]
                        }
                    ],
                },
            ],
        },
    }
    p = tmp_path / "export.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_value_unwraps_typeson_wrapper_but_not_inbound_rows():
    assert ydb._value({"$": [1, {"a": 2}]}) == {"a": 2}
    assert ydb._value({"$": [1, {"a": 2}], "$types": {}}) == {"a": 2}
    assert ydb._value({"expression": "x"}) == {"expression": "x"}  # inbound row: value as-is


def test_converters_match_yomitan_bank_shapes():
    v = {
        "expression": "猫",
        "reading": "ねこ",
        "definitionTags": "n",
        "glossary": ["cat"],
        "sequence": 3,
    }
    assert ydb._term_bank_entry(v) == ["猫", "ねこ", "n", "", 0, ["cat"], 3, ""]
    assert ydb._term_meta_entry({"expression": "猫", "mode": "freq", "data": {"x": 1}}) == [
        "猫",
        "freq",
        {"x": 1},
    ]
    assert ydb._tag_bank_entry({"name": "n", "category": "pos", "order": 2, "notes": "noun"}) == [
        "n",
        "pos",
        2,
        "noun",
        0,
    ]


def test_read_header_validates_dexie_and_totals(tmp_path):
    tables, total = ydb.read_header(_dexie(tmp_path))
    assert total == 5
    assert [t["name"] for t in tables] == ["dictionaries", "terms", "termMeta", "tagMeta"]


def test_read_header_rejects_non_dexie(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"options":{"profiles":[]}}')  # a settings export, not a dexie DB export
    with pytest.raises(ydb.YomitanDbImportError):
        ydb.read_header(p)


def test_import_reconstructs_zips_that_the_loader_reads(tmp_path):
    out = tmp_path / "dicts"
    seen = []
    paths = ydb.import_database(_dexie(tmp_path), out, progress=lambda d, t: seen.append((d, t)))
    names = {p.name for p in paths}
    assert names == {"TestDict.zip", "TestFreq.zip"}
    assert seen[-1] == (5, 5)  # progress reached the header total

    import dicthelp

    from overlay.app.yomitan_import import classify_zip

    d = dicthelp.load_dict(out / "TestDict.zip")
    assert d.title == "TestDict"
    ents = d.lookup("猫")
    assert ents and ents[0].reading == "ねこ"

    # content-based classification: the def dict vs the freq dict built from termMeta
    assert classify_zip(out / "TestDict.zip") == "dict"
    assert classify_zip(out / "TestFreq.zip") == "freq"
