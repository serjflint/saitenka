#!/usr/bin/env python3
"""Per-span latency percentiles from a Saitenka CTF telemetry trace.

Reads one ``trace-*.json`` (Chrome Trace Format: ``{"traceEvents":[...]}``) emitted by
``overlay.app.telemetry`` and prints p50/p75/p90/p95/p99 per span, in milliseconds — the UX
critical-path spans (tooltip open, scroll frame, sub-seek, …) first so the delays that matter read at
a glance. Stdlib only, so it runs under the py-spy profiling venv without extra deps.

Spans that carry a ``kind`` attribute (``render``/``tip_compose``/``prefetch_decode`` —
base vs nested vs clicked vs engaged) are reported per kind (``tip_compose[nested]``), so a nested/clicked
paint's latency doesn't hide inside the base aggregate.

    python tools/span_percentiles.py <trace.json> [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Spans on the interaction critical path — the "does it feel instant" surface the reader hovers/scrolls
# through. Listed in the order they're surfaced (headline end-to-end delays first, then their pieces).
CRITICAL = [
    "tooltip_show",  # end-to-end hover → drawn (the headline tooltip open delay)
    "scroll_frame",  # scroll input → redraw (the scroll delay)
    "sub_seek",  # subtitle nav seek
    "render",  # panel build
    "tip_compose",  # composite + blit
    "measure",  # panel measure
    "hit_test",  # per-tick hover resolve
    "upload",  # OSD texture upload
    "subtitle_render",
    "cue_redraw",
    "dict_sql",  # dictionary lookup
    "prefetch_decode",  # scroll-ahead warm
]
_ORDER = {name: i for i, name in enumerate(CRITICAL)}


def _pct(sorted_ms: list[float], q: float) -> float:
    """Nearest-rank percentile (matches tools/trace_report.py)."""
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, max(0, math.ceil(q * len(sorted_ms)) - 1))
    return sorted_ms[idx]


# Spans reported per ``kind`` attribute — the same paint span covers visibly-distinct interactions
# (base vs nested scan popup vs clicked cross-ref nav vs the off-thread engaged compose), so a nested or
# clicked tail must not hide inside the base aggregate. Other spans aggregate by name alone.
_SPLIT_BY_KIND = {"render", "tip_compose", "prefetch_decode"}


def _label(ev: dict) -> str:
    """The grouping label for one span event: ``name`` normally, ``name[kind]`` for a kind-split span
    that carries a ``kind`` attribute (CTF args)."""
    name = ev["name"]
    if name in _SPLIT_BY_KIND:
        kind = (ev.get("args") or {}).get("kind")
        if kind:
            return f"{name}[{kind}]"
    return name


def _base(label: str) -> str:
    """The span name without any ``[kind]`` suffix — for the critical-path ordering + the ``*`` mark."""
    return label.split("[", 1)[0]


def _durations_by_span(events: list[dict]) -> dict[str, list[float]]:
    by_name: dict[str, list[float]] = {}
    for ev in events:
        if ev.get("ph") != "X" or "dur" not in ev:
            continue
        by_name.setdefault(_label(ev), []).append(ev["dur"] / 1000.0)  # µs → ms
    return by_name


def _rows(by_name: dict[str, list[float]]) -> list[tuple]:
    rows = []
    for name, durs in by_name.items():
        s = sorted(durs)
        rows.append(
            (
                name,
                len(s),
                _pct(s, 0.50),
                _pct(s, 0.75),
                _pct(s, 0.90),
                _pct(s, 0.95),
                _pct(s, 0.99),
                s[-1],
                sum(s) / len(s),
            )
        )
    # Critical-path spans first (in CRITICAL order, by base name), then the rest by total time descending.
    rows.sort(key=lambda r: (_ORDER.get(_base(r[0]), 10_000), -(r[1] * r[8])))
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trace", type=Path, help="path to a trace-*.json CTF file")
    ap.add_argument("--label", default="", help="header label (e.g. the benchmark name)")
    args = ap.parse_args(argv)

    if not args.trace.exists():
        print(
            f"span-percentiles: no trace at {args.trace} (telemetry produced none)", file=sys.stderr
        )
        return 1
    events = json.loads(args.trace.read_text(encoding="utf-8")).get("traceEvents", [])
    rows = _rows(_durations_by_span(events))
    if not rows:
        print(f"span-percentiles: {args.trace} has no spans", file=sys.stderr)
        return 1

    title = f"span latency percentiles (ms){f' — {args.label}' if args.label else ''}"
    print(f"\n{title}")
    print(
        f"{'span':<32} {'n':>6} {'p50':>8} {'p75':>8} {'p90':>8} {'p95':>8} {'p99':>8} {'max':>8} {'mean':>8}"
    )
    print("-" * 106)
    for name, n, p50, p75, p90, p95, p99, mx, mean in rows:
        mark = "*" if _base(name) in _ORDER else " "
        print(
            f"{mark}{name:<31} {n:>6} {p50:>8.2f} {p75:>8.2f} {p90:>8.2f} "
            f"{p95:>8.2f} {p99:>8.2f} {mx:>8.2f} {mean:>8.2f}"
        )
    print("\n* = UX critical-path span")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
