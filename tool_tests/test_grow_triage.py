"""Tests run by `poe loop-tools-test`, or explicitly:
    uv run python -m pytest tool_tests/test_grow_triage.py

Only the PURE scorer is tested — the real gatherers are subprocess glue (ruff/git/gh), like
`sharpen_triage`, which carries no unit test. The one invariant worth locking is that the composite is a
PRODUCT of the two axes, not a sum (the Grow↔Sharpen distinction).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import grow_triage as gt


def _cand(
    module: str,
    *,
    fan_in: int,
    churn: int = 0,
    priv_seam: int = 0,
    survivors=None,
    untested: bool = False,
):
    return gt.Candidate(
        module=module,
        tests=[],
        fan_in=fan_in,
        churn=churn,
        priv_seam=priv_seam,
        survivors=survivors,
        untested=untested,
    )


def test_an_untested_valuable_module_outranks_a_tested_god_object():
    # Untested = under-spec 1.0, so equal value ranks it above a seam-heavy tested module.
    god = _cand("controller.py", fan_in=20, churn=86, priv_seam=147)  # tested, the old #1
    untested = _cand("newfeature.py", fan_in=20, churn=86, untested=True)  # same value, no tests
    gt.score_candidates([god, untested])
    assert untested.score > god.score


def test_high_value_but_fully_specified_scores_zero():
    # max fan-in, but zero under-specification → product is zero (a sum would rank it top)
    c = _cand("hot.py", fan_in=44, priv_seam=0, survivors=0)
    other = _cand("mild.py", fan_in=1, priv_seam=5, survivors=3)
    gt.score_candidates([c, other])
    assert c.score == 0.0
    assert other.score > 0.0


def test_under_specified_but_worthless_scores_zero():
    # heavily under-specified, but zero value (no fan-in, no churn) → product is zero
    c = _cand("dead.py", fan_in=0, churn=0, priv_seam=9, survivors=9)
    other = _cand("live.py", fan_in=10, churn=4, priv_seam=3)
    gt.score_candidates([c, other])
    assert c.score == 0.0
    assert other.score > 0.0


def test_the_bullseye_both_axes_high_ranks_first():
    bull = _cand("panel.py", fan_in=44, churn=8, priv_seam=6, survivors=4)
    value_only = _cand("config.py", fan_in=37, churn=6, priv_seam=0)
    uspec_only = _cand("leaf.py", fan_in=0, churn=0, priv_seam=8)
    cands = [value_only, bull, uspec_only]
    gt.score_candidates(cands)
    cands.sort(key=lambda c: -c.score)
    assert cands[0] is bull


def test_excluded_candidates_are_skipped_by_the_scorer():
    live = _cand("a.py", fan_in=10, priv_seam=5)
    dead = _cand("b.py", fan_in=99, priv_seam=99)
    dead.excluded = "open-PR: x"
    gt.score_candidates([live, dead])
    assert dead.score == 0.0  # untouched — never scored
    assert live.score > 0.0


def test_optional_signals_absent_fall_back_to_the_seam_proxy():
    # No mutation campaign supplied: the always-available seam signal still provides a weak score.
    c = _cand("x.py", fan_in=5, priv_seam=4, survivors=None)
    gt.score_candidates([c])
    assert c.underspec > 0.0  # driven by the always-available seam signal


def test_partial_survivor_input_keeps_missing_modules_unknown():
    measured = _cand("measured.py", fan_in=5, priv_seam=4, survivors=1)
    unknown = _cand("unknown.py", fan_in=5, priv_seam=4, survivors=None)

    gt.score_candidates([measured, unknown])

    assert unknown.underspec == 0.5
