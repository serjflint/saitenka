"""In-process operation timing — a minimal, dependency-free ring buffer of recent op durations.

Deliberately small: it exists to give a crash report and ``doctor`` a live latency snapshot (was the
overlay janking right before it crashed? is the panel cache thrashing right now?) without standing up
the full OpenTelemetry stack from ROADMAP.md ("Observability"). If that plan is ever built, this module
is the thing it replaces — not a first stage of it.

Always on (no opt-in flag): recording is a lock-guarded ``deque.append`` — cheaper than the poll tick's
own IPC round-trip — so there's no meaningful cost to gate.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager

_MAXLEN = 200  # per-op samples kept; old ones fall off, no unbounded growth

_lock = threading.Lock()
_ops: dict[str, deque[float]] = {}


def record(op: str, ms: float) -> None:
    with _lock:
        d = _ops.get(op)
        if d is None:
            d = deque[float](maxlen=_MAXLEN)
            _ops[op] = d
        d.append(ms)


@contextmanager
def timed(op: str) -> Generator[None]:
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(op, (time.perf_counter() - t0) * 1000.0)


def _stats(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)

    def p(q: float) -> float:
        return s[min(len(s) - 1, int(q * len(s)))]

    return {
        "n": len(s),
        "p50": p(0.50),
        "p95": p(0.95),
        "max": s[-1],
        "mean": statistics.fmean(s),
    }


def snapshot() -> dict[str, dict[str, float]]:
    """Point-in-time percentile summary per instrumented op, over each op's last ``_MAXLEN`` samples."""
    with _lock:
        items = {op: list(d) for op, d in _ops.items()}
    return {op: _stats(samples) for op, samples in items.items() if samples}


def rss_mb() -> float | None:
    """Current resident set size in MB, or ``None`` if unavailable. A gauge, not a history — call it
    at the moment you need it (crash capture, ``doctor``); it costs one syscall via ``psutil``, cheap
    enough for that but not for the per-tick hot path."""
    try:
        import psutil
    except ImportError:  # pragma: no cover — psutil is a core dep; defensive only
        return None
    try:
        return float(psutil.Process().memory_info().rss) / 1e6
    except Exception:  # pragma: no cover — never let a diagnostic reading crash the caller
        return None


def reset() -> None:
    """Test hook — clear all recorded timings."""
    with _lock:
        _ops.clear()
