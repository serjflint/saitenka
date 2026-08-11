"""Tests for the grow coverage-context producer (aggregation core). Run explicitly:
    uv run python -m pytest tools/test_grow_contexts.py

Only the pure `aggregate` is tested — the real coverage run is subprocess glue, like sharpen_triage.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grow_contexts as gc

BASE = Path("/repo/src/overlay").resolve()


def _f(rel: str) -> str:
    return str(BASE / rel)


def test_aggregate_counts_uncovered_plus_weakly_covered():
    measured = [_f("app/x.py")]
    missing = {_f("app/x.py"): [10, 11]}  # 2 uncovered lines
    contexts = {
        _f("app/x.py"): {
            1: ["tests.a", "tests.b"],  # well-specified (2 contexts) — not weak
            2: ["tests.a"],  # weak (1 context)
            3: [""],  # incidental-only (0 real contexts) — weak
        }
    }
    out = gc.aggregate(measured, BASE, lambda f: missing[f], lambda f: contexts[f])
    assert out["app/x.py"]["under_spec"] == 2 + 2  # 2 uncovered + (line2 + line3) weak
    assert out["app/x.py"]["test_nodeids"] == ["tests.a", "tests.b"]


def test_aggregate_ignores_files_outside_the_source_base():
    outside = "/usr/lib/python/site-packages/dep.py"
    out = gc.aggregate([outside], BASE, lambda _: [1], lambda _: {})
    assert out == {}


def test_aggregate_a_fully_specified_module_scores_zero():
    measured = [_f("app/clean.py")]
    contexts = {
        _f("app/clean.py"): {1: ["t.a", "t.b"], 2: ["t.a", "t.c", "t.d"]}
    }  # all ≥2 contexts
    out = gc.aggregate(measured, BASE, lambda _: [], lambda f: contexts[f])
    assert out["app/clean.py"]["under_spec"] == 0
