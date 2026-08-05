"""Tests for the perf rot-guard's compare seam. Run explicitly (tools/ is outside `poe all`):
    uv run python -m pytest tools/test_perf_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import perf_gate as pg


def test_regression_past_tolerance_is_flagged():
    base = {"full_median_ms": 10.0, "full_p99_ms": 20.0}
    cur = {"full_median_ms": 16.0, "full_p99_ms": 20.0}  # median +60% > +50%
    regs = pg.regressions(base, cur, 0.5)
    assert [r[0] for r in regs] == ["full_median_ms"]
    assert regs[0][3] == 1.6  # ratio reported


def test_within_tolerance_passes():
    base = {"full_median_ms": 10.0, "full_p99_ms": 20.0}
    cur = {"full_median_ms": 14.0, "full_p99_ms": 29.0}  # +40% / +45%, both under +50%
    assert pg.regressions(base, cur, 0.5) == []


def test_improvement_is_never_a_regression():
    base = {"full_median_ms": 10.0, "full_p99_ms": 20.0}
    cur = {"full_median_ms": 6.0, "full_p99_ms": 9.0}  # faster
    assert pg.regressions(base, cur, 0.5) == []


def test_missing_metric_is_skipped_not_failed():
    # An older-schema baseline without one gated key must not spuriously fail (or KeyError).
    base = {"full_median_ms": 10.0}  # no full_p99_ms
    cur = {"full_median_ms": 100.0, "full_p99_ms": 999.0}
    regs = pg.regressions(base, cur, 0.5)
    assert [r[0] for r in regs] == ["full_median_ms"]  # p99 skipped, median still gated


def test_zero_baseline_is_skipped():
    # A non-positive baseline (unmeasured) can't define a ratio → skip, never divide-by-zero.
    base = {"full_median_ms": 0.0, "full_p99_ms": 20.0}
    cur = {"full_median_ms": 5.0, "full_p99_ms": 21.0}
    assert pg.regressions(base, cur, 0.5) == []


def test_tolerance_boundary_is_inclusive():
    # Exactly at base*(1+tol) is NOT a regression (strict >), so the ratchet doesn't flap on the edge.
    base = {"full_median_ms": 10.0, "full_p99_ms": 20.0}
    cur = {"full_median_ms": 15.0, "full_p99_ms": 30.0}  # exactly +50%
    assert pg.regressions(base, cur, 0.5) == []
