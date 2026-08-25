"""Tests run by `poe loop-tools-test`, or explicitly:
uv run python -m pytest tool_tests/test_perf_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import perf_gate as pg


def test_regression_past_tolerance_is_flagged():
    base = {"synth_median_ms": 10.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 16.0, "synth_p99_ms": 20.0}  # median +60% > +50%
    regs = pg.regressions(base, cur, 0.5)
    assert [r[0] for r in regs] == ["synth_median_ms"]
    assert regs[0][3] == 1.6  # ratio reported


def test_within_tolerance_passes():
    base = {"synth_median_ms": 10.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 14.0, "synth_p99_ms": 29.0}  # +40% / +45%, both under +50%
    assert pg.regressions(base, cur, 0.5) == []


def test_improvement_is_never_a_regression():
    base = {"synth_median_ms": 10.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 6.0, "synth_p99_ms": 9.0}  # faster
    assert pg.regressions(base, cur, 0.5) == []


def test_missing_metric_is_skipped_not_failed():
    # An older-schema baseline without one gated key must not spuriously fail (or KeyError).
    base = {"synth_median_ms": 10.0}  # no synth_p99_ms
    cur = {"synth_median_ms": 100.0, "synth_p99_ms": 999.0}
    regs = pg.regressions(base, cur, 0.5)
    assert [r[0] for r in regs] == ["synth_median_ms"]  # p99 skipped, median still gated


def test_zero_baseline_is_skipped():
    # A non-positive baseline (unmeasured) can't define a ratio → skip, never divide-by-zero.
    base = {"synth_median_ms": 0.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 5.0, "synth_p99_ms": 21.0}
    assert pg.regressions(base, cur, 0.5) == []


def test_tolerance_boundary_is_inclusive():
    # Exactly at base*(1+tol) is NOT a regression (strict >), so the ratchet doesn't flap on the edge.
    base = {"synth_median_ms": 10.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 15.0, "synth_p99_ms": 30.0}  # exactly +50%
    assert pg.regressions(base, cur, 0.5) == []


def test_per_metric_default_gates_median_tight_but_p99_loose():
    # No CLI --tolerance → per-metric GATED: median +50%, p99 +100% (tail-noisy). A median +60% is a
    # regression; a p99 +60% is NOT (under its own +100%). This is the noise-characterized default.
    base = {"synth_median_ms": 10.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 16.0, "synth_p99_ms": 32.0}  # median +60%, p99 +60%
    regs = pg.regressions(base, cur, None)
    assert [r[0] for r in regs] == ["synth_median_ms"]  # only median trips its tighter tolerance


def test_per_metric_default_flags_p99_past_its_own_tolerance():
    # A genuine tail rot (p99 > 2x) trips even p99's loose +100%.
    base = {"synth_median_ms": 10.0, "synth_p99_ms": 20.0}
    cur = {"synth_median_ms": 10.0, "synth_p99_ms": 41.0}  # p99 +105% > +100%
    regs = pg.regressions(base, cur, None)
    assert [r[0] for r in regs] == ["synth_p99_ms"]
