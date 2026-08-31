"""Property tests for app/fsrs.py — hardens `uv run poe mutate fsrs` survivors.

Companion to the example-based test_fsrs.py. Each property below targets a class of surviving
mutants found by a fresh `poe mutate fsrs` run (59.00% survival rate, the worst of the four
audited modules) — a boundary comparison, an arithmetic swap, or a continue/break control-flow
flip that the example suite never happened to exercise. All properties drive the PUBLIC surface
(`retrievability`, `rareness_band`, `load_knownness` + `KnownSnap`) — never the private `_classify_state`
/ `_build_card_info` / `_build_states` / `_wordlike` helpers directly — per this repo's "test the
seam, not private methods" rule; oracles below re-derive the documented contract independently.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import example, given, settings

from saitenka.app import fsrs
from saitenka.app.features.tooltip.tooltip_panel import rareness_value
from saitenka.app.fsrs import (
    FORGOTTEN_R,
    FSRS_DEFAULT_DECAY,
    MATURE_IVL,
    RARENESS_COMMON_MAX,
    RARENESS_UNCOMMON_MAX,
    load_knownness,
    rareness_band,
    retrievability,
)

# ---------------------------------------------------------------------------
# DB fixture builders (extend test_fsrs.py's _build_minimal_anki2 pattern)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE col (id INTEGER PRIMARY KEY, mod INTEGER, ver INTEGER);
CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT);
CREATE TABLE cards (
    id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, type INTEGER, queue INTEGER,
    ivl INTEGER, data TEXT
);
CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER);
CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, kind BLOB);
CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, config BLOB);
"""


def _make_db(path: Path, notes: list[dict[str, Any]], now: float) -> None:
    """notes: [{"nid", "mid", "flds", "cards": [{"ctype", "ivl", "s", "decay", "elapsed_days"}]}].

    `elapsed_days=None` means "no revlog row" (never reviewed). `revlog.id` is a real Anki
    epoch-ms timestamp and must be globally unique across every card; on a collision (two cards
    computing the same ms, or a sub-millisecond `elapsed_days` truncating to the same integer) the
    id is nudged forward by whole milliseconds until unique — a 1ms nudge is ~1e-8 days, far below
    any threshold this suite's assertions depend on.
    """
    con = sqlite3.connect(str(path))
    con.executescript(_SCHEMA)
    con.execute("INSERT INTO col VALUES (1, 1000, 22)")
    con.execute("INSERT INTO deck_config VALUES (1,'Default',?)", (b"",))
    con.execute("INSERT INTO decks VALUES (1,'Default',?)", (b"",))
    cid = 1
    used_ts: set[int] = set()
    for note in notes:
        con.execute("INSERT INTO notes VALUES (?,?,?)", (note["nid"], note["mid"], note["flds"]))
        for card in note["cards"]:
            data = (
                json.dumps({"s": card["s"], "decay": card["decay"]})
                if card.get("s") is not None
                else ""
            )
            con.execute(
                "INSERT INTO cards VALUES (?,?,?,?,?,?,?)",
                (cid, note["nid"], 1, card["ctype"], -1, card["ivl"], data),
            )
            if card.get("elapsed_days") is not None:
                ts = int((now - card["elapsed_days"] * 86400) * 1000)
                while ts in used_ts:
                    ts += 1
                used_ts.add(ts)
                con.execute("INSERT INTO revlog VALUES (?,?)", (ts, cid))
            cid += 1
    con.commit()
    con.close()


def _snap(notes: list[dict[str, Any]], now: float = 1_700_000_000.0):
    """Build the fixture, freeze fsrs.time.time() to `now` (the sanctioned clock seam per
    AGENTS.md), and return the resulting KnownSnap.

    Uses `pytest.MonkeyPatch.context()` and its own `tempfile.TemporaryDirectory()` rather than
    the `monkeypatch`/`tmp_path` fixtures: fixtures are function-scoped and don't reset between
    `@given(...)`-generated examples, which Hypothesis flags as a health-check failure.
    """
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
        db = Path(tmp) / "col.anki2"
        _make_db(db, notes, now=now)
        mp.setattr(fsrs.time, "time", lambda: now)
        return load_knownness(db)


# ---------------------------------------------------------------------------
# 1. retrievability — kills the `s <= 0` → `s <= 1` boundary mutant (line 63)
# ---------------------------------------------------------------------------


@given(
    s=st.floats(min_value=1e-6, max_value=1000.0, allow_nan=False, allow_infinity=False),
    elapsed=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    decay=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@example(s=0.5, elapsed=30.0, decay=0.1542)  # 0 < s <= 1: kills `s <= 0` mutated to `s <= 1`
@settings(max_examples=100, deadline=None)
def test_retrievability_matches_reference_for_any_positive_s(s, elapsed, decay):
    """Any strictly positive `s` yields a real value — including 0 < s <= 1, the exact range a
    `s <= 0` → `s <= 1` mutant would wrongly collapse to None."""
    factor = 0.9 ** (1.0 / decay) - 1.0
    expected = (1.0 + factor * elapsed / s) ** decay
    got = retrievability(s, elapsed, decay)
    assert got is not None
    assert abs(got - expected) < 1e-6


@given(
    s=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False),
    elapsed=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    decay=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, deadline=None)
def test_retrievability_none_for_nonpositive_s(s, elapsed, decay):
    assert retrievability(s, elapsed, decay) is None


# ---------------------------------------------------------------------------
# 2. The rareness pill's two halves: the band (here) and the `Nk` formatting (the panel side)
# ---------------------------------------------------------------------------


@given(rank=st.integers(min_value=0, max_value=200_000))
@example(rank=999)  # just below the "k" cutoff
@example(rank=1000)  # exact thousand: "1k" not "1.0k"
@example(rank=1001)  # just above: falls to the decimal branch
@example(rank=1999)
@example(rank=2000)  # exact even thousand again
@settings(max_examples=200, deadline=None)
def test_rareness_value_matches_documented_formatting(rank):
    """Below 1000 render the bare int; at/above 1000 render `Nk` on an exact multiple, else one
    decimal place of thousands."""
    r = round(float(rank))
    expected = (f"{r // 1000}k" if r % 1000 == 0 else f"{r / 1000:.1f}k") if r >= 1000 else str(r)
    assert rareness_value(float(rank)) == expected


@given(rank=st.integers(min_value=0, max_value=200_000))
@example(rank=RARENESS_COMMON_MAX)
@example(rank=RARENESS_COMMON_MAX + 1)
@example(rank=RARENESS_UNCOMMON_MAX)
@example(rank=RARENESS_UNCOMMON_MAX + 1)
@settings(max_examples=200, deadline=None)
def test_rareness_band_is_monotone_in_the_rank(rank):
    """Rarer never bands as commoner — the invariant behind the two cutoffs, independent of where
    they sit or which colour draws them."""
    order = {"common": 0, "uncommon": 1, "rare": 2}
    assert order[rareness_band(float(rank))] <= order[rareness_band(float(rank) + 1)]


# ---------------------------------------------------------------------------
# 3. State classification — kills the ctype 1|3 NumberReplacer and ivl>=mature_ivl mutants
#    (lines 228, 230, 232), driven end-to-end through load_knownness.
# ---------------------------------------------------------------------------


def _classify_oracle(
    ctype: int, r: float | None, ivl: int, forgotten_r: float, mature_ivl: int
) -> str | None:
    """Independent re-derivation of the documented ctype/retrievability/interval contract —
    never imports the private `_classify_state` it's meant to check."""
    if ctype == 0:
        return None  # "new" cards never enter the snapshot
    if ctype in {1, 3}:
        return "learning"
    if r is not None and r < forgotten_r:
        return "forgotten"
    if ivl and ivl >= mature_ivl:
        return "known"
    return "young"


@given(
    ctype=st.sampled_from([0, 1, 2, 3]),
    ivl=st.integers(min_value=0, max_value=400),
    s=st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)
@example(ctype=1, ivl=0, s=100.0)  # kills case `1|3` -> `0|3` (ctype=1 must still hit "learning")
@example(ctype=3, ivl=0, s=100.0)  # kills case `1|3` -> `1|4` (ctype=3 must still hit "learning")
@example(ctype=2, ivl=MATURE_IVL, s=200.0)  # exact ivl==mature_ivl boundary: "known"
@example(ctype=2, ivl=MATURE_IVL - 1, s=200.0)  # just below: "young", kills `>=` -> `!=`
@example(ctype=2, ivl=MATURE_IVL + 1, s=200.0)  # just above: "known"
@example(ctype=2, ivl=0, s=200.0)  # falsy ivl: kills `ivl and ...` -> `ivl or ...`
@settings(max_examples=100, deadline=None)
def test_classify_state_matches_documented_contract(ctype, ivl, s):
    """Freshly reviewed (elapsed≈0 ⇒ r≈1.0, safely above FORGOTTEN_R) so only the ctype/ivl branches
    are under test — the r<forgotten_r boundary itself is a float-equality razor's edge, effectively
    unreachable through the ms-rounded elapsed pipeline, so it's left as a documented near-equivalent."""
    now = 1_700_000_000.0
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": "猫",
            "cards": [
                {
                    "ctype": ctype,
                    "ivl": ivl,
                    "s": s,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 0.0,
                }
            ],
        }
    ]
    snap = _snap(notes, now=now)

    r = retrievability(s, 0.0, FSRS_DEFAULT_DECAY)
    expected = _classify_oracle(ctype, r, ivl, FORGOTTEN_R, MATURE_IVL)
    assert snap.state("猫") == expected


# ---------------------------------------------------------------------------
# 4. Best-card-per-note dedup — kills the `k = s * r` arithmetic swaps and `k > prev["k"]`
#    comparison mutants (lines 255, 259, 262).
# ---------------------------------------------------------------------------


@given(
    s1=st.floats(min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False),
    s2=st.floats(min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False),
    # Capped at 1.0 (not e.g. 10.0): elapsed/s > ~2 drives (1 + factor*elapsed/s) negative, and a
    # negative base to a fractional `decay` power returns a complex number in Python — a real,
    # separate numerical edge in retrievability() itself, out of scope for this dedup-logic test.
    elapsed1=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    elapsed2=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@example(s1=200.0, s2=10.0, elapsed1=1.0, elapsed2=1.0)  # card 1 clearly wins
@example(
    s1=10.0, s2=200.0, elapsed1=1.0, elapsed2=1.0
)  # card 2 clearly wins (tests `>` not just first-wins)
@settings(max_examples=100, deadline=None)
def test_best_review_card_per_note_wins(s1, s2, elapsed1, elapsed2):
    """Two review cards (ctype=2) on the same note, same high ivl (so classification hinges purely
    on the winning card's retrievability, not the ivl branch): the higher-`k` card's state wins,
    and a tie keeps the first (`k > prev["k"]`, strict)."""
    now = 1_700_000_000.0
    decay = FSRS_DEFAULT_DECAY
    ivl = MATURE_IVL + 10
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": "猫",
            "cards": [
                {"ctype": 2, "ivl": ivl, "s": s1, "decay": decay, "elapsed_days": elapsed1},
                {"ctype": 2, "ivl": ivl, "s": s2, "decay": decay, "elapsed_days": elapsed2},
            ],
        }
    ]
    snap = _snap(notes, now=now)

    r1 = retrievability(s1, elapsed1, decay)
    r2 = retrievability(s2, elapsed2, decay)
    k1 = (s1 or 0.0) * (r1 if r1 is not None else 1.0)
    k2 = (s2 or 0.0) * (r2 if r2 is not None else 1.0)
    winner_r = r2 if k2 > k1 else r1  # strict >, so a tie keeps card 1

    expected = _classify_oracle(2, winner_r, ivl, FORGOTTEN_R, MATURE_IVL)
    assert snap.state("猫") == expected


@given(s_review=st.floats(min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=25, deadline=None)
def test_nonreview_card_never_outranks_a_review_card(s_review):
    """A `new` card (ctype=0, k forced to 0.0 regardless of its own `s`) must never beat a review
    card in the dedup — kills the `ctype == 2` → `ctype != 2` mutant on line 259. Distinct ivl per
    card is essential: `ctype == 2` False for both card AND `ctype != 2` True for card only differs
    observably if the wrong card winning actually changes the resulting classification."""
    now = 1_700_000_000.0
    decay = FSRS_DEFAULT_DECAY
    ivl = MATURE_IVL + 10
    notes = [
        {"ctype": 0, "ivl": 0, "s": 9999.0, "decay": decay, "elapsed_days": None},
        {"ctype": 2, "ivl": ivl, "s": s_review, "decay": decay, "elapsed_days": 1.0},
    ]
    notes = [{"nid": 1, "mid": 1, "flds": "猫", "cards": notes}]
    snap = _snap(notes, now=now)

    r = retrievability(s_review, 1.0, decay)
    expected = _classify_oracle(2, r, ivl, FORGOTTEN_R, MATURE_IVL)
    assert snap.state("猫") == expected


@given(s_review=st.floats(min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=25, deadline=None)
def test_relearning_card_never_outranks_a_review_card(s_review):
    """A `relearning` card (ctype=3, k forced to 0.0 same as any non-review ctype) must never beat
    a review card either — kills `ctype == 2` mutated to `ctype >= 2` on line 259, which the
    ctype=0 case above can't distinguish (0 >= 2 is False, same as 0 == 2)."""
    now = 1_700_000_000.0
    decay = FSRS_DEFAULT_DECAY
    ivl = MATURE_IVL + 10
    notes = [
        # No revlog: if this wrongly scores via s*1.0 instead of being zeroed, its huge s dominates.
        {"ctype": 3, "ivl": 0, "s": 9999.0, "decay": decay, "elapsed_days": None},
        {"ctype": 2, "ivl": ivl, "s": s_review, "decay": decay, "elapsed_days": 1.0},
    ]
    notes = [{"nid": 1, "mid": 1, "flds": "猫", "cards": notes}]
    snap = _snap(notes, now=now)

    r = retrievability(s_review, 1.0, decay)
    expected = _classify_oracle(2, r, ivl, FORGOTTEN_R, MATURE_IVL)
    assert snap.state("猫") == expected


def test_review_card_with_no_revlog_scores_via_s_alone_not_zeroed():
    """A ctype=2 card with NO revlog row (`elapsed=None` ⇒ `r=None`) still contributes `k = s * 1.0`
    — kills the `r if r is not None else 1.0` → `else 0.0` NumberReplacer on line 259, which would
    silently zero out every never-reviewed review card in the dedup. Distinct ivl per card matters:
    if both cards classify to the same state regardless of which one "wins", the mutant is invisible."""
    now = 1_700_000_000.0
    decay = FSRS_DEFAULT_DECAY
    notes = [
        # No revlog: must win via k = 500 * 1.0 = 500, not 500 * 0.0 = 0.
        {"ctype": 2, "ivl": MATURE_IVL + 10, "s": 500.0, "decay": decay, "elapsed_days": None},
        {"ctype": 2, "ivl": 0, "s": 1.0, "decay": decay, "elapsed_days": 0.5},
    ]
    notes = [{"nid": 1, "mid": 1, "flds": "猫", "cards": notes}]
    snap = _snap(notes, now=now)
    # k1 = 500 * 1.0 = 500 (r=None fallback), k2 = 1.0 * retrievability(1, 0.5, decay) < 1 — card 1
    # must win; under the `else 0.0` mutant k1 would be 0.0 and card 2 (ivl=0 -> "young" instead of
    # "known") would wrongly win instead.
    r2 = retrievability(1.0, 0.5, decay)
    assert r2 is not None
    assert 1.0 * r2 < 500.0  # sanity: the test setup itself assumes card 1 has the higher k
    expected = _classify_oracle(2, None, MATURE_IVL + 10, FORGOTTEN_R, MATURE_IVL)
    assert snap.state("猫") == expected
    assert expected == "known"  # sanity: pins the exact regression the mutant would cause


def test_dedup_tie_keeps_the_first_card_not_the_last():
    """Two review cards with an EXACT tie in `k`: both `elapsed_days=None` (no revlog row) so
    `k = s * 1.0` with no timestamp arithmetic involved at all — the only way to get a *bit-for-bit*
    tie, since two real revlog timestamps can never collide (each `revlog.id` is a globally unique
    epoch-ms). `k > prev["k"]` is strict, so the first-inserted card must win — kills `k > prev["k"]`
    mutated to `k >= prev["k"]` on line 262, which would let a tie flip to the later card."""
    now = 1_700_000_000.0
    decay = FSRS_DEFAULT_DECAY
    s = 200.0  # same s, both elapsed_days=None -> identical k = s * 1.0, no float drift possible
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": "猫",
            "cards": [
                {"ctype": 2, "ivl": MATURE_IVL + 5, "s": s, "decay": decay, "elapsed_days": None},
                {"ctype": 2, "ivl": 0, "s": s, "decay": decay, "elapsed_days": None},
            ],
        }
    ]
    snap = _snap(notes, now=now)
    # First card's ivl (mature) must decide the outcome — a `>=` mutant would let the second
    # (ivl=0, falsy) card win the tie instead, changing "known" to "young".
    expected = _classify_oracle(2, None, MATURE_IVL + 5, FORGOTTEN_R, MATURE_IVL)
    assert snap.state("猫") == expected


# ---------------------------------------------------------------------------
# 5. _build_states loop — kills the continue→break mutants (lines 331, 335) by proving a note
#    AFTER a skipped one is still processed.
# ---------------------------------------------------------------------------

_TERMS = ["猫", "犬", "鳥", "魚", "馬", "牛", "羊", "豚"]


@st.composite
def _note_specs(draw):
    """A shuffled mix of skip-worthy notes (new card, or a card with no extractable term) and
    valid notes with distinct single-kanji terms — order matters: a `break` mutant would stop
    processing at the first skip and silently drop every valid note that comes after it."""
    n_valid = draw(st.integers(min_value=2, max_value=len(_TERMS)))
    terms = draw(st.permutations(_TERMS))[:n_valid]
    kinds = draw(st.permutations(["new", "empty"] + ["valid"] * n_valid))
    valid_iter = iter(terms)
    specs = []
    for kind in kinds:
        if kind == "valid":
            specs.append({"kind": "valid", "term": next(valid_iter)})
        else:
            specs.append({"kind": kind})
    return specs


@given(_note_specs())
@settings(max_examples=50, deadline=None)
def test_valid_notes_after_a_skipped_note_are_still_processed(specs):
    now = 1_700_000_000.0
    notes = []
    for i, spec in enumerate(specs, start=1):
        if spec["kind"] == "new":
            notes.append(
                {
                    "nid": i,
                    "mid": 1,
                    "flds": "猫",
                    "cards": [
                        {"ctype": 0, "ivl": 0, "s": None, "decay": None, "elapsed_days": None}
                    ],
                }
            )
        elif spec["kind"] == "empty":
            # No word-like field anywhere → term extraction fails → the `if not term: continue` path.
            notes.append(
                {
                    "nid": i,
                    "mid": 1,
                    "flds": "",
                    "cards": [
                        {
                            "ctype": 2,
                            "ivl": MATURE_IVL + 5,
                            "s": 100.0,
                            "decay": FSRS_DEFAULT_DECAY,
                            "elapsed_days": 1.0,
                        }
                    ],
                }
            )
        else:
            notes.append(
                {
                    "nid": i,
                    "mid": 1,
                    "flds": spec["term"],
                    "cards": [
                        {
                            "ctype": 2,
                            "ivl": MATURE_IVL + 5,
                            "s": 200.0,
                            "decay": FSRS_DEFAULT_DECAY,
                            "elapsed_days": 1.0,
                        }
                    ],
                }
            )

    snap = _snap(notes, now=now)

    valid_terms = [s["term"] for s in specs if s["kind"] == "valid"]
    for term in valid_terms:
        assert snap.state(term) == "known", (
            f"{term} missing/wrong — a `continue` may have become `break`"
        )


# ---------------------------------------------------------------------------
# 6. Term/reading extraction — kills the `_wordlike` length-boundary and the fallback-scan
#    `i + 1 < len(parts)` mutants (lines 128, 287, 288) end-to-end.
# ---------------------------------------------------------------------------


def test_wordlike_length_boundary_twelve_is_included_thirteen_is_not():
    """`_wordlike`'s `len(t) <= 12` boundary, observed through whether the term is recognized at
    all — kills `<= 12` mutated to `< 12` / `!= 12` / `<= 13`."""
    now = 1_700_000_000.0
    term_12 = "猫" * 12
    term_13 = "猫" * 13
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": term_12,
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        },
        {
            "nid": 2,
            "mid": 1,
            "flds": term_13,
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        },
    ]
    snap = _snap(notes, now=now)
    assert snap.state(term_12) == "known", "a 12-char word-like term must still be recognized"
    assert snap.state(term_13) is None, (
        "a 13-char term exceeds _wordlike's length cap and is dropped"
    )


def test_fallback_scan_reading_taken_from_next_field_not_the_term_itself():
    """When the word-like term isn't the last field, the *next* field becomes its reading — kills
    `parts[i + 1]` mutated to `parts[i ** 1]` (which would silently re-read the term as its own
    "reading"), and the `i + 1 < len(parts)` boundary mutants."""
    now = 1_700_000_000.0
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": "猫\x1fねこ",  # term then a distinct reading field
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        },
        {
            "nid": 2,
            "mid": 1,
            "flds": "犬",  # term is the ONLY field: no next field to read from
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        },
    ]
    snap = _snap(notes, now=now)
    assert snap.state("猫") == "known"
    assert snap.state("ねこ") == "known", "reading form must resolve to the same state as the term"
    assert snap.state("犬") == "known", (
        "single-field note: no next field, must not crash or misread"
    )


def test_fallback_scan_reading_is_the_next_field_not_the_previous_one():
    """The term isn't always field 0 — with a filler field before it, `parts[i + 1]` must still
    mean the field AFTER the term, not `parts[i - 1]` (which happens to equal `parts[i + 1]` only
    by coincidence in a 2-field note, hence a dedicated 3-field case): kills `parts[i + 1]` mutated
    to `parts[i - 1]` on line 288."""
    now = 1_700_000_000.0
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": "filler\x1f猫\x1fねこ",  # filler, then term, then its reading
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        }
    ]
    snap = _snap(notes, now=now)
    assert snap.state("ねこ") == "known", (
        "reading must come from the field AFTER the term (`parts[i - 1]` would wrongly grab "
        "'filler' instead)"
    )


# ---------------------------------------------------------------------------
# 6b. Named-field matching (`_TERM_FIELDS`/`_READING_FIELDS` by field NAME, via a notetype's
#     `fields` table) — an entire code path with zero prior coverage (every test above only ever
#     exercised the no-fields-table fallback scan). Kills lines 299, 302, 304, 307.
# ---------------------------------------------------------------------------


def _make_db_with_field_names(
    notes: list[dict[str, Any]], field_names: dict[int, list[str]], now: float
):
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
        db = Path(tmp) / "col.anki2"
        _make_db(db, notes, now=now)
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT)")
        for ntid, names in field_names.items():
            for ord_, name in enumerate(names):
                con.execute("INSERT INTO fields VALUES (?,?,?)", (ntid, ord_, name))
        con.commit()
        con.close()
        mp.setattr(fsrs.time, "time", lambda: now)
        return load_knownness(db)


def _card(s: float = 200.0, elapsed_days: float = 1.0) -> dict[str, Any]:
    return {
        "ctype": 2,
        "ivl": MATURE_IVL + 5,
        "s": s,
        "decay": FSRS_DEFAULT_DECAY,
        "elapsed_days": elapsed_days,
    }


def test_named_field_shorter_than_declared_falls_back_safely_not_emptily():
    """A notetype can declare more fields than a given note actually stores (e.g. a field added
    after the note was created) — `_extract_term_reading` must degrade gracefully, not raise and
    blank out the ENTIRE snapshot: kills `i < len(parts)` mutated to `i is len(parts)` on line 299,
    which raises IndexError as soon as the loop index reaches exactly `len(parts)`."""
    notes = [{"nid": 1, "mid": 1, "flds": "犬", "cards": [_card()]}]  # only 1 field...
    field_names = {1: ["expression", "reading"]}  # ...but the notetype declares 2
    snap = _make_db_with_field_names(notes, field_names, now=1_700_000_000.0)
    assert snap.state("犬") == "known", (
        "the whole snapshot must not go empty from an uncaught IndexError"
    )


def test_named_term_field_not_hijacked_by_an_earlier_unnamed_wordlike_field():
    """A field with no recognized name (e.g. "notes") sitting BEFORE the real, correctly-named term
    field must never become the term — kills the `not term` → `term` (line 302) and `not reading`
    → `reading` (line 304) guards being deleted, both of which break named matching so badly it
    falls through to the fallback scan and silently grabs the wrong (first) field instead."""
    notes = [{"nid": 1, "mid": 1, "flds": "ねこ\x1f犬", "cards": [_card()]}]
    field_names = {1: ["reading", "expression"]}  # "reading" (unrelated decoy) comes first
    snap = _make_db_with_field_names(notes, field_names, now=1_700_000_000.0)
    assert snap.state("犬") == "known", (
        "the NAMED term field ('expression') must win, not the decoy"
    )
    assert snap.state("ねこ") == "known", (
        "the NAMED reading field must still resolve — a deleted `not` on line 304 leaves it empty"
    )


def test_named_term_field_first_match_wins_not_last():
    """When TWO fields both carry a recognized term-field name, the FIRST word-like one wins (the
    `not term` guard stops later matches from overwriting it) — kills `and` → `or` on line 302,
    under which ANY field whose name is in `_TERM_FIELDS` unconditionally overwrites, so the LAST
    one would win instead."""
    notes = [{"nid": 1, "mid": 1, "flds": "犬\x1f猫", "cards": [_card()]}]
    field_names = {1: ["vocab", "expression"]}  # both are valid term-field names
    snap = _make_db_with_field_names(notes, field_names, now=1_700_000_000.0)
    assert snap.state("犬") == "known", "the FIRST matching term field must win"
    assert snap.state("猫") is None, "the second term-named field must not overwrite the first"


def test_named_matching_does_not_rerun_fallback_scan_once_term_is_found():
    """Once the term is resolved via a named field match, the loop must not re-run the fallback
    scan afterwards — kills `not term and parts` mutated to `not term or parts` on line 307, which
    reruns the fallback scan unconditionally and lets an earlier unnamed decoy field overwrite an
    already-correct, name-matched term."""
    notes = [{"nid": 1, "mid": 1, "flds": "猫\x1f犬", "cards": [_card()]}]
    field_names = {1: ["notes", "expression"]}  # decoy field, then the real (named) term field
    snap = _make_db_with_field_names(notes, field_names, now=1_700_000_000.0)
    assert snap.state("犬") == "known", "the name-matched term must not be clobbered by a re-scan"
    assert snap.state("猫") is None, "the unnamed decoy field must never become the term"


# ---------------------------------------------------------------------------
# 7. _strip_markup — kills the invisible/bidi-char ZeroIterationForLoop mutant (line 107-108).
# ---------------------------------------------------------------------------


def test_zero_width_space_is_stripped_from_the_term():
    now = 1_700_000_000.0
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": "猫\u200b",  # zero-width space embedded in the term field
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        }
    ]
    snap = _snap(notes, now=now)
    assert snap.state("猫") == "known", (
        "a ZWSP-contaminated field must still resolve to the clean term"
    )


# ---------------------------------------------------------------------------
# 8. _build_states — kills `reading != term` mutated to `reading <= term` (line 339).
# ---------------------------------------------------------------------------


def test_reading_lexically_greater_than_term_is_still_recorded():
    """`reading != term` must record ANY distinct reading, not just a lexicographically smaller
    one — a `!=` → `<=` mutant only drops the recording when `reading > term`, which a reading
    that happens to sort before its term (as in every other test's 猫/ねこ pair, since kana sorts
    below kanji by codepoint) would never expose."""
    now = 1_700_000_000.0
    term, reading = "あ", "ん"  # U+3042 < U+3093: reading sorts strictly AFTER the term
    assert reading > term
    notes = [
        {
            "nid": 1,
            "mid": 1,
            "flds": f"{term}\x1f{reading}",
            "cards": [
                {
                    "ctype": 2,
                    "ivl": MATURE_IVL + 5,
                    "s": 200.0,
                    "decay": FSRS_DEFAULT_DECAY,
                    "elapsed_days": 1.0,
                }
            ],
        }
    ]
    snap = _snap(notes, now=now)
    assert snap.state(term) == "known"
    assert snap.state(reading) == "known", (
        "reading > term lexically must still be recorded — a `<=` mutant silently drops it"
    )


# ---------------------------------------------------------------------------
# 9. _record_state priority ordering — kills `priority(st) > priority(cur)` mutated to
#    `<=` / `!=` / `==` / `<` / `is` / `is not` (line 318), via TWO notes sharing the exact same
#    term with different states — the only way to reach the "already recorded, compare priority"
#    branch at all (a single note's own term is only ever recorded once).
# ---------------------------------------------------------------------------


def _note_with_term(nid: int, term: str, ivl: int, elapsed_days: float) -> dict[str, Any]:
    return {
        "nid": nid,
        "mid": 1,
        "flds": term,
        "cards": [
            {
                "ctype": 2,
                "ivl": ivl,
                "s": 200.0,
                "decay": FSRS_DEFAULT_DECAY,
                "elapsed_days": elapsed_days,
            }
        ],
    }


def test_higher_priority_state_upgrades_an_existing_lower_one():
    """Two notes recording the SAME word: a "young" card followed by a "known" one for the exact
    same term must upgrade the recorded state — kills `> ` mutated to `<=` / `!=` / `<` / `is` on
    line 318, which would leave the word stuck at "young"."""
    now = 1_700_000_000.0
    notes = [
        _note_with_term(1, "猫", ivl=0, elapsed_days=1.0),  # -> "young" (ivl falsy)
        _note_with_term(2, "猫", ivl=MATURE_IVL + 5, elapsed_days=1.0),  # -> "known"
    ]
    snap = _snap(notes, now=now)
    assert snap.state("猫") == "known", "the later, higher-priority match must win"


def test_lower_priority_state_never_downgrades_an_existing_higher_one():
    """The reverse order: a "known" card followed by a "young" one for the same term must NOT
    downgrade — kills `>` mutated to `<=` / `!=` / `is not` on line 318, which would let the later,
    lower-priority match silently overwrite it."""
    now = 1_700_000_000.0
    notes = [
        _note_with_term(1, "猫", ivl=MATURE_IVL + 5, elapsed_days=1.0),  # -> "known"
        _note_with_term(2, "猫", ivl=0, elapsed_days=1.0),  # -> "young" (ivl falsy)
    ]
    snap = _snap(notes, now=now)
    assert snap.state("猫") == "known", "a later, lower-priority match must not downgrade it"
