"""Stage 5: pathological-cold benchmark corpus — the discovery helper that finds the worst
first-lookup words by querying the consolidated dict DB for the largest glossary payloads."""

import importlib.util
import json
import sqlite3
from pathlib import Path

from overlay.app.dictdb import DictionaryDb

BENCH_PATH = Path(__file__).resolve().parent.parent / "examples" / "bench_responsiveness.py"
_DICT_ID = 1


def _bench_module():
    spec = importlib.util.spec_from_file_location("bench_responsiveness", BENCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(path: Path, entries) -> DictionaryDb:
    """A real consolidated dict DB (schema via ``DictionaryDb.open``), with ``entries`` inserted
    under one ``dict_id`` — matches how multiple imported dictionaries actually share one DB file."""
    db = DictionaryDb.open(path)
    conn = sqlite3.connect(path)
    for i, (term, reading, glossary) in enumerate(entries, 1):
        conn.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?,?)",
            (_DICT_ID, i, term, reading, json.dumps(glossary, ensure_ascii=False), ""),
        )
        conn.execute("INSERT INTO keys VALUES(?,?,?)", (_DICT_ID, term, i))
    conn.commit()
    conn.close()
    return db


def test_discover_pathological_returns_largest_glossaries(tmp_path):
    db = _make_db(
        tmp_path / "d.sqlite",
        [
            ("小", "しょう", ["x"]),  # tiny payload
            ("大", "だい", ["long gloss " * 200]),  # the biggest payload
            ("中", "ちゅう", ["medium gloss " * 20]),  # middle
        ],
    )
    mod = _bench_module()
    rows = mod.discover_pathological(db, _DICT_ID, n=2)
    assert len(rows) == 2
    # ordered by glossary payload size, descending — the biggest entry first
    assert rows[0][0] == "大"
    assert rows[1][0] == "中"
    # each row carries (term, reading, payload_bytes)
    _term, reading, size = rows[0]
    assert reading == "だい"
    assert isinstance(size, int) and size > rows[1][2]


def test_discover_pathological_caps_at_n(tmp_path):
    db = _make_db(tmp_path / "d.sqlite", [(f"語{i}", f"ご{i}", ["g" * (i + 1)]) for i in range(10)])
    mod = _bench_module()
    assert len(mod.discover_pathological(db, _DICT_ID, n=3)) == 3
