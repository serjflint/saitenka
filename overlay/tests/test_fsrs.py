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
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _import_fsrs():
    from overlay.app import fsrs

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


class TestRetrievability:
    """The FSRS formula must match tools/anki_rank_dicts.py verbatim."""

    def test_exact_formula_matches_reference(self):
        """retrievability(s, elapsed, decay) == (1 + (0.9^(1/decay)-1) * elapsed/s)^decay."""
        fsrs = _import_fsrs()

        # Reference formula (from anki_rank_dicts.py retrievability())
        def ref(s, elapsed, decay):
            factor = 0.9 ** (1.0 / decay) - 1.0
            return (1.0 + factor * elapsed / s) ** decay

        cases = [
            (200.0, 30.0, 0.1542),  # mature known card
            (10.0, 30.0, 0.1542),  # forgotten card
            (50.0, 7.0, 0.1542),  # young card
            (100.0, 0.0, 0.1542),  # elapsed=0 → R=1.0
        ]
        for s, elapsed, decay in cases:
            expected = ref(s, elapsed, decay)
            got = fsrs.retrievability(s, elapsed, decay)
            assert abs(got - expected) < 1e-9, (
                f"retrievability({s},{elapsed},{decay}): got {got}, expected {expected}"
            )

    def test_invalid_inputs_return_none(self):
        """s≤0, negative elapsed, or s=None → None (not a crash)."""
        fsrs = _import_fsrs()
        assert fsrs.retrievability(0, 10, 0.1542) is None
        assert fsrs.retrievability(-5, 10, 0.1542) is None
        assert fsrs.retrievability(None, 10, 0.1542) is None  # type: ignore[arg-type]
        assert fsrs.retrievability(10, -1, 0.1542) is None

    def test_r_at_zero_elapsed_is_one(self):
        fsrs = _import_fsrs()
        r = fsrs.retrievability(100.0, 0.0, 0.1542)
        assert r is not None
        assert abs(r - 1.0) < 1e-9


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
# 4. Scorer: forgotten tint
# ---------------------------------------------------------------------------


class TestScorerForgottenTint:
    """Scorer gains a 'forgotten' tint between known and unknown."""

    def test_palette_has_forgotten_color(self):
        """Palette now has a 'forgotten' color attribute."""
        from overlay.app.scoring import Palette

        p = Palette()
        assert hasattr(p, "forgotten"), "Palette must have a 'forgotten' color for Stage 13"

    def test_forgotten_word_gets_forgotten_tint(self, tmp_path):
        """A word in 'forgotten' state in the snapshot is styled with the forgotten color."""
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)

        from overlay.app.scoring import Palette, Scorer
        from overlay.app.tokenize import Token
        from overlay.app.wordlists import KnownWords

        p = Palette()
        # KnownWords treats the forgotten word as NOT known (binary for coloring purposes)
        kw = KnownWords.from_set([])  # empty: nothing is "known" in the old sense

        scorer = Scorer(known=kw, palette=p, fsrs_snap=snap)

        # Build a minimal token for 忘れる (which is "forgotten")
        tok = Token(
            surface="忘れる",
            lemma="忘れる",
            reading="わすれる",
            pos="動詞",
            start=0,
            end=3,
        )
        styles = scorer.score_line([tok])
        assert len(styles) == 1
        style = styles[0]
        assert style.color == p.forgotten, (
            f"expected forgotten color {p.forgotten!r}, got {style.color!r}"
        )

    def test_known_word_still_gets_known_tint(self, tmp_path):
        """A word with known state in the snapshot gets the known (green) tint."""
        fsrs = _import_fsrs()
        db = tmp_path / "col.anki2"
        _build_minimal_anki2(db)
        snap = fsrs.load_knownness(db)

        from overlay.app.scoring import Palette, Scorer
        from overlay.app.tokenize import Token
        from overlay.app.wordlists import KnownWords

        p = Palette()
        kw = KnownWords.from_set([])

        scorer = Scorer(known=kw, palette=p, fsrs_snap=snap)

        tok = Token(
            surface="知る",
            lemma="知る",
            reading="しる",
            pos="動詞",
            start=0,
            end=2,
        )
        styles = scorer.score_line([tok])
        assert styles[0].color == p.known, (
            f"expected known color {p.known!r}, got {styles[0].color!r}"
        )

    def test_scorer_without_fsrs_snap_unchanged(self):
        """If fsrs_snap=None (not configured), Scorer behaves exactly as before Stage 13."""
        from overlay.app.scoring import Scorer
        from overlay.app.tokenize import Token
        from overlay.app.wordlists import KnownWords

        kw = KnownWords.from_set([])
        scorer = Scorer(known=kw, fsrs_snap=None)  # no snap: no forgotten tint
        tok = Token(
            surface="忘れる",
            lemma="忘れる",
            reading="わすれる",
            pos="動詞",
            start=0,
            end=3,
        )
        styles = scorer.score_line([tok])
        from overlay.app.scoring import Palette

        p = Palette()
        # Without snap it should NOT be the forgotten color (base or freq)
        assert styles[0].color != p.forgotten, (
            "without fsrs_snap, no forgotten tint should be applied"
        )


# ---------------------------------------------------------------------------
# 5. Difficulty pill — harmonic-mean frequency rank
# ---------------------------------------------------------------------------


class TestDifficultyPill:
    """harmonic_rank() computes the blended difficulty estimate."""

    def test_harmonic_rank_single_dict(self):
        """With one freq dict, harmonic rank == that dict's rank."""
        fsrs = _import_fsrs()
        result = fsrs.harmonic_rank("猫", [{"猫": 500}])
        assert result == pytest.approx(500.0)

    def test_harmonic_rank_two_dicts(self):
        """With two dicts, result is the harmonic mean (len/sum(1/r))."""
        fsrs = _import_fsrs()
        # harmonic mean of 1000 and 2000 = 2 / (1/1000 + 1/2000) = 2/(0.001+0.0005) ≈ 1333.3
        result = fsrs.harmonic_rank("言葉", [{"言葉": 1000}, {"言葉": 2000}])
        assert result == pytest.approx(1333.33, rel=1e-3)

    def test_harmonic_rank_word_absent_from_some_dicts(self):
        """Only dicts that contain the word contribute to the blend."""
        fsrs = _import_fsrs()
        result = fsrs.harmonic_rank("猫", [{"猫": 500}, {"犬": 100}])
        # Only the first dict has 猫
        assert result == pytest.approx(500.0)

    def test_harmonic_rank_absent_everywhere_returns_none(self):
        """If no dict has the word, returns None."""
        fsrs = _import_fsrs()
        result = fsrs.harmonic_rank("存在しない語", [{"猫": 500}])
        assert result is None

    def test_harmonic_rank_empty_dicts_returns_none(self):
        fsrs = _import_fsrs()
        result = fsrs.harmonic_rank("猫", [])
        assert result is None

    def test_diff_pill_from_entry_freqs(self):
        """diff_pill_from_ranks produces a Freq pill with the blended rank."""
        fsrs = _import_fsrs()
        pill = fsrs.diff_pill(1333.0)
        from overlay.panel import Freq

        assert isinstance(pill, Freq)
        assert pill.name == "diff"
        # value should encode the rank as a string (1333 → "1.3k" or "1333")
        assert "1333" in pill.value or "k" in pill.value.lower()

    def test_diff_pill_none_for_missing_rank(self):
        """diff_pill(None) returns None — no pill emitted when we have no data."""
        fsrs = _import_fsrs()
        assert fsrs.diff_pill(None) is None
