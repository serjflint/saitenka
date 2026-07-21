"""Stage 5: pathological-cold benchmark corpus — the discovery helper that finds the worst
first-lookup words by querying a built SQLite dict index for the largest glossary payloads."""

import importlib.util
import json
import sqlite3
from pathlib import Path

BENCH_PATH = Path(__file__).resolve().parent.parent / "examples" / "bench_responsiveness.py"


def _bench_module():
    spec = importlib.util.spec_from_file_location("bench_responsiveness", BENCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_index(path: Path, entries):
    """Build a minimal dict-cache SQLite with the real schema (meta/entries/keys)."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta(k TEXT, v TEXT)")
    conn.execute(
        "CREATE TABLE entries(id INTEGER PRIMARY KEY, term TEXT, reading TEXT, "
        "glossary TEXT, tags TEXT)"
    )
    conn.execute("CREATE TABLE keys(key TEXT, id INT)")
    conn.execute("INSERT INTO meta VALUES('title', 'T')")
    for i, (term, reading, glossary) in enumerate(entries, 1):
        conn.execute(
            "INSERT INTO entries VALUES(?,?,?,?,?)",
            (i, term, reading, json.dumps(glossary, ensure_ascii=False), ""),
        )
        conn.execute("INSERT INTO keys VALUES(?,?)", (term, i))
    conn.commit()
    conn.close()


def test_discover_pathological_returns_largest_glossaries(tmp_path):
    db = tmp_path / "d.sqlite"
    _make_index(
        db,
        [
            ("小", "しょう", ["x"]),  # tiny payload
            ("大", "だい", ["long gloss " * 200]),  # the biggest payload
            ("中", "ちゅう", ["medium gloss " * 20]),  # middle
        ],
    )
    mod = _bench_module()
    rows = mod.discover_pathological(str(db), n=2)
    assert len(rows) == 2
    # ordered by glossary payload size, descending — the biggest entry first
    assert rows[0][0] == "大"
    assert rows[1][0] == "中"
    # each row carries (term, reading, payload_bytes)
    _term, reading, size = rows[0]
    assert reading == "だい"
    assert isinstance(size, int) and size > rows[1][2]


def test_discover_pathological_caps_at_n(tmp_path):
    db = tmp_path / "d.sqlite"
    _make_index(db, [(f"語{i}", f"ご{i}", ["g" * (i + 1)]) for i in range(10)])
    mod = _bench_module()
    assert len(mod.discover_pathological(str(db), n=3)) == 3
