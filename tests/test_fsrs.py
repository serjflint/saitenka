"""Stage 13: Tests for app/fsrs.py — FSRS knownness snapshot + difficulty pill.

TDD: tests written BEFORE the implementation. They must fail initially.

The FSRS retrievability math must be cross-checked against the exact formula in
tools/anki_rank_dicts.py:
    factor = 0.9^(1/decay) - 1
    R = (1 + factor * elapsed / s)^decay
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_fsrs():
    from saitenka_wordstate import fsrs

    return fsrs


def _build_minimal_anki2(path: Path) -> None:
    """Build a minimal valid collection.anki2 with a handful of known/forgotten cards."""
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE col (id INTEGER PRIMARY KEY, mod INTEGER, ver INTEGER);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT);
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            nid INTEGER,
            did INTEGER,
            type INTEGER,
            queue INTEGER,
            ivl INTEGER,
            data TEXT
        );
        CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER);
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, kind BLOB);
        CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, config BLOB);
        INSERT INTO col VALUES (1, 1000, 22);
        """
    )
    # Note-type id we'll use
    mid = 1234

    # Use distinct revlog IDs (revlog.id = timestamp ms, must be unique)
    elapsed_days = 30
    base_ts = int((time.time() - elapsed_days * 86400) * 1000)

    # 1. A "known" card: s=200, R ≈ 0.99 at 30 days elapsed → should be "known"
    con.execute(
        "INSERT INTO notes VALUES (1, ?, ?)",
        (mid, "知る\x1fしる"),
    )
    con.execute(
        "INSERT INTO cards VALUES (1,1,1,2,-1,200,?)",
        (json.dumps({"s": 200.0, "decay": 0.1542}),),
    )
    con.execute("INSERT INTO revlog VALUES (?,1)", (base_ts,))

    # 2. A "forgotten" card: s=10, R ≈ 0.48 at 30 days → below 0.85 threshold → "forgotten"
    con.execute(
        "INSERT INTO notes VALUES (2, ?, ?)",
        (mid, "忘れる\x1fわすれる"),
    )
    con.execute(
        "INSERT INTO cards VALUES (2,2,1,2,-1,10,?)",
        (json.dumps({"s": 10.0, "decay": 0.1542}),),
    )
    con.execute("INSERT INTO revlog VALUES (?,2)", (base_ts + 1,))

    # 3. A "new" card: type=0 → "new"
    con.execute(
        "INSERT INTO notes VALUES (3, ?, ?)",
        (mid, "新しい\x1fあたらしい"),
    )
    con.execute("INSERT INTO cards VALUES (3,3,1,0,0,0,'')", ())

    # 4. A "learning" card: type=1 → "learning"
    con.execute(
        "INSERT INTO notes VALUES (4, ?, ?)",
        (mid, "学ぶ\x1fまなぶ"),
    )
    con.execute("INSERT INTO cards VALUES (4,4,1,1,1,0,'')", ())

    # A deck and deck_config row so the loader doesn't crash on empty tables
    con.execute("INSERT INTO deck_config VALUES (1,'Default',?)", (b"",))
    con.execute("INSERT INTO decks VALUES (1,'Default',?)", (b"",))

    con.commit()
    con.close()


def test_collection_snapshot_classifies_and_queries_forms(tmp_path):
    fsrs = _import_fsrs()
    db = tmp_path / "col.anki2"
    _build_minimal_anki2(db)

    snap = fsrs.load_knownness(db)

    assert snap.state("知る") == "known"
    assert snap.state("忘れる") == "forgotten"
    assert snap.state("新しい") is None
    assert snap.state("存在しない語") is None
    assert snap.is_known("知る")
    assert snap.is_forgotten("忘れる")
    assert snap.is_known("知る", "しる")


def test_missing_collection_produces_an_empty_snapshot(tmp_path):
    fsrs = _import_fsrs()
    snap = fsrs.load_knownness(tmp_path / "missing.anki2")
    assert snap.state("何でも") is None
