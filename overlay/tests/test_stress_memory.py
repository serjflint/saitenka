"""Memory-regression companion to test_stress.py, via memray (pytest-memray's ``limit_memory``).

Deliberately NOT in the fast gate: memray's native allocation tracking has real overhead (this file
alone runs ~1 minute), so it's `slow`-marked and excluded from `poe test`/`poe all`. Run explicitly:

    uv run poe stress-memory

Reuses test_stress.py's hermetic harness (synthetic tall entries, no real dicts) scaled up to run long
enough to exercise round-over-round growth, the same signal examples/bench_responsiveness.py --stress
reports for a real dict set. The `limit_memory` ceiling is a generous regression guard, not a precise
budget — see saitenka-perf-module memory notes for the (still-open) investigation into where growth
comes from at this synthetic scale vs. a real dict corpus.
"""

from __future__ import annotations

import pytest
from test_stress import PANEL_CACHE_MAX, _churn, _reader

pytestmark = pytest.mark.slow

# Several passes over a corpus well past the cache cap, so eviction — and any per-eviction retention
# bug — is exercised repeatedly, not just once.
_ROUNDS = 8
_CORPUS = [f"語{i:03d}" for i in range(PANEL_CACHE_MAX + 24)]


@pytest.mark.limit_memory("2 GB")  # generous regression guard (measured peak ~1.4GB, mostly
# legitimate PIL Image buffers rendering many large synthetic panels) — catches a 2x+ blowup, not
# tuned as a precise budget.
def test_sustained_churn_stays_within_memory_ceiling():
    r = _reader()
    for _ in range(_ROUNDS):
        for term in _CORPUS:
            _churn(r, term)
    assert len(r._panel_cache) <= PANEL_CACHE_MAX
