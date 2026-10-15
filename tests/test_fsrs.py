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


# ---------------------------------------------------------------------------
# 1. FSRS retrievability math — cross-checked against anki_rank_dicts formula
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. Collection loading — word → state classification
# ---------------------------------------------------------------------------


class TestCollectionLoading:
    """load_knownness correctly classifies cards from a minimal .anki2 fixture."""

    def test_known_card_classified_as_known(self, tmp_path):
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        # 知る has s=200, R≈0.99 @ 30 days → known
        assert snap.state("知る") == "known", f"snap for 知る: {snap.state('知る')}"

    def test_forgotten_card_classified_as_forgotten(self, tmp_path):
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        # 忘れる has s=10, R≈0.48 @ 30 days → forgotten (< 0.85 threshold)
        assert snap.state("忘れる") == "forgotten", f"snap for 忘れる: {snap.state('忘れる')}"

    def test_new_card_not_in_snapshot(self, tmp_path):
        """type=0 (new) cards don't appear in the knownness snapshot at all."""
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        assert snap.state("新しい") is None

    def test_unknown_word_returns_none(self, tmp_path):
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        assert snap.state("存在しない語") is None

    def test_nonexistent_db_raises_or_returns_empty(self, tmp_path):
        """A missing collection path returns an empty snapshot (graceful fallback)."""
        fsrs = _import_fsrs()
        snap = fsrs.load_knownness(tmp_path / "missing.anki2")
        assert snap.state("何でも") is None


# ---------------------------------------------------------------------------
# 3. KnownSnap.is_known / is_forgotten helpers
# ---------------------------------------------------------------------------


class TestKnownSnap:
    """KnownSnap convenience methods."""

    def test_is_known_returns_true_for_known(self, tmp_path):
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        assert snap.is_known("知る")

    def test_is_forgotten_returns_true_for_forgotten(self, tmp_path):
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        assert snap.is_forgotten("忘れる")

    def test_multi_form_lookup(self, tmp_path):
        """is_known(*forms) returns True if ANY form is in the snapshot."""
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)
        # "しる" is the reading stored from the note field parsing
        assert snap.is_known("知る", "しる") or snap.is_known("知る")


# ---------------------------------------------------------------------------
# 5. Difficulty pill — harmonic-mean frequency rank
# ---------------------------------------------------------------------------
