#!/usr/bin/env python3
"""Per-span latency percentiles from a Saitenka CTF telemetry trace.

Reads one ``trace-*.json`` (Chrome Trace Format: ``{"traceEvents":[...]}``) emitted by
``saitenka.app.telemetry`` and prints p50/p75/p90/p95/p99 per span, in milliseconds. Interactive
(main-thread) spans — whose latency IS the perceived delay — are marked ``*`` and listed first
(render/measure/dict_sql included: a warm hover skips them, but a cold miss builds them inline). Only
the off-thread ``prefetch_decode`` warm is ``~`` (never blocks), except its user-awaited ``engaged_*``
kinds. Stdlib only, so it runs under the py-spy profiling venv without extra deps.

Spans that carry a ``kind`` attribute (``render``/``tip_compose``/``prefetch_decode`` — base vs nested
vs clicked vs the off-thread engaged composes ``engaged``/``engaged_nested``/``engaged_nav``/
``engaged_open``) are reported per kind (``tip_compose[nested]``, ``prefetch_decode[engaged_open]``), so a
nested/clicked paint's latency doesn't hide inside the base aggregate.

    python tools/span_percentiles.py <trace.json> [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Spans that reach the poll/paint (main) thread — their latency IS the perceived delay, so a tail here
# is a real "does it feel instant" regression. Marked ``*``, ordered headline-first. Note render/measure/
# dict_sql: a WARM hover skips them (prefetch populated the panel_cache + memoized the measure), but a
# COLD miss builds them INLINE in show_tooltip_impl on the main thread — the tracked cold-first-paint,
# and exactly the cost that overshoots the frame budget on slower hardware. So they gate.
INTERACTIVE = [
    "tooltip_show",  # end-to-end hover → drawn (the headline tooltip open delay)
    "scroll_frame",  # scroll input → redraw (the scroll delay)
    "sub_seek",  # subtitle nav seek
    "render",  # panel build — inline on a cold miss
    "tip_compose",  # composite + blit on show
    "measure",  # panel measure — inline on a cold miss
    "hit_test",  # per-tick hover resolve
    "upload",  # OSD texture upload
    "subtitle_render",
    "cue_redraw",
    "dict_sql",  # dictionary lookup — inline on a cold miss
    "sidebar_click",  # sidebar action → redraw (may touch SQLite)
    "mined_store_write",  # main-thread mined-card store write on a mine
    "backlog_write",  # main-thread backlog store write on a bookmark/mine
]
# prefetch_decode is the ONLY off-thread span: the workers warm the panel_cache ahead of the hover, so
# head/warm/head_ahead NEVER block the interaction. Marked ``~`` — big numbers here are fine.
BACKGROUND = ["prefetch_decode"]
# ...EXCEPT these prefetch_decode kinds: the user hovered/clicked a word prefetch missed and WAITS for
# this off-thread compose before the popup shows — a perceived cold-open delay, so they mark ``*``.
INTERACTIVE_KINDS = {
    f"prefetch_decode[{k}]" for k in ("engaged", "engaged_nested", "engaged_nav", "engaged_open")
}
_ORDER = {name: i for i, name in enumerate(INTERACTIVE + BACKGROUND)}
_INTERACTIVE = set(INTERACTIVE)
_BACKGROUND = set(BACKGROUND)


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
    """The span name without any ``[kind]`` suffix — for the critical-path ordering + the mark."""
    return label.split("[", 1)[0]


def _mark(label: str) -> str:
    """``*`` main-thread (perceived delay), ``~`` off-thread background prefetch warm, blank otherwise.
    Per-kind so a warm ``prefetch_decode[head]`` (``~``) and a user-awaited ``prefetch_decode[engaged]``
    (``*``, they wait for it) mark differently."""
    if label in INTERACTIVE_KINDS or _base(label) in _INTERACTIVE:
        return "*"
    return "~" if _base(label) in _BACKGROUND else " "


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
        print(
            f"{_mark(name)}{name:<31} {n:>6} {p50:>8.2f} {p75:>8.2f} {p90:>8.2f} "
            f"{p95:>8.2f} {p99:>8.2f} {mx:>8.2f} {mean:>8.2f}"
        )
    print("\n* = interactive (main-thread) span — its latency IS the perceived delay")
    print(
        "    (render/measure/dict_sql: a warm hover skips them; a COLD miss builds them inline — the "
        "cold-first-paint that overshoots budget on slower HW)"
    )
    print("~ = off-thread prefetch warm — never blocks the interaction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
