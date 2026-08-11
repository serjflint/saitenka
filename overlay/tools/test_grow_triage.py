"""Tests run by `poe loop-tools-test`, or explicitly:
    uv run python -m pytest tools/test_grow_triage.py

Only the PURE scorer is tested — the real gatherers are subprocess glue (ruff/git/gh), like
`sharpen_triage`, which carries no unit test. The one invariant worth locking is that the composite is a
PRODUCT of the two axes, not a sum (the Grow↔Sharpen distinction).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import grow_triage as gt
from tool_json import InstrumentError


def _cand(
    module: str,
    *,
    fan_in: int,
    churn: int = 0,
    priv_seam: int = 0,
    survivors=None,
    dead_ctx=None,
    untested: bool = False,
):
    return gt.Candidate(
        module=module,
        tests=[],
        fan_in=fan_in,
        churn=churn,
        priv_seam=priv_seam,
        survivors=survivors,
        dead_ctx=dead_ctx,
        untested=untested,
    )


def test_an_untested_valuable_module_outranks_a_tested_god_object():  # C5
    # The run-1 inversion: a tested god-object (huge private-attr seam) topped the list while genuinely
    # untested valuable code was invisible. Untested = under-spec 1.0, so equal value now ranks it higher.
    god = _cand("controller.py", fan_in=20, churn=86, priv_seam=147)  # tested, the old #1
    untested = _cand("newfeature.py", fan_in=20, churn=86, untested=True)  # same value, no tests
    gt.score_candidates([god, untested])
    assert untested.score > god.score


def test_high_value_but_fully_specified_scores_zero():
    # max fan-in, but zero under-specification → product is zero (a sum would rank it top)
    c = _cand("hot.py", fan_in=44, priv_seam=0, survivors=0, dead_ctx=0)
    other = _cand("mild.py", fan_in=1, priv_seam=5, survivors=3, dead_ctx=2)
    gt.score_candidates([c, other])
    assert c.score == 0.0
    assert other.score > 0.0


def test_under_specified_but_worthless_scores_zero():
    # heavily under-specified, but zero value (no fan-in, no churn) → product is zero
    c = _cand("dead.py", fan_in=0, churn=0, priv_seam=9, survivors=9, dead_ctx=9)
    other = _cand("live.py", fan_in=10, churn=4, priv_seam=3)
    gt.score_candidates([c, other])
    assert c.score == 0.0
    assert other.score > 0.0


def test_the_bullseye_both_axes_high_ranks_first():
    bull = _cand("panel.py", fan_in=44, churn=8, priv_seam=6, survivors=4, dead_ctx=3)
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


def test_optional_signals_absent_do_not_crash_and_read_as_zero():
    # survivors / dead_ctx are None (no campaign / contexts supplied) → treated as 0, still scores on seam
    c = _cand("x.py", fan_in=5, priv_seam=4, survivors=None, dead_ctx=None)
    gt.score_candidates([c])
    assert c.underspec > 0.0  # driven by the always-available seam signal


def test_coverage_context_evidence_prevents_a_false_testless_label():
    merged = gt.merge_test_evidence(["app/x.py"], {}, {"app/x.py": ["tests/test_x.py"]})
    assert merged == {"app/x.py": ["tests/test_x.py"]}


def test_module_without_static_or_context_evidence_remains_testless():
    assert gt.merge_test_evidence(["app/x.py"], {}, {}) == {"app/x.py": []}


def test_legacy_context_json_is_rejected_with_a_regeneration_error(tmp_path):
    path = tmp_path / "contexts.json"
    path.write_text('{"app/x.py": 3}\n', encoding="utf-8")
    with pytest.raises(InstrumentError, match="regenerate"):
        gt._load_contexts("contexts.json", tmp_path)


def test_context_v2_carries_test_nodeids_into_attribution(tmp_path):
    path = tmp_path / "contexts.json"
    path.write_text(
        '{"version":2,"modules":{"app/x.py":{"under_spec":3,'
        '"test_nodeids":["tests/test_x.py::test_one|run"]}}}\n',
        encoding="utf-8",
    )
    counts, tests = gt._load_contexts("contexts.json", tmp_path)
    assert counts == {"app/x.py": 3}
    assert tests == {"app/x.py": ["tests/test_x.py"]}
