# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Turn a `saitenka report` bundle into a readable performance/diagnostics report.

`saitenka report` zips a Chrome-trace `telemetry/trace.json` (X = complete spans, C = counter
samples) plus the JSONL `overlay.log`. This distills them into: a span table with self-time
(inferred by time-containment per thread — the tracer emits no parent id), the final value of every
pull-based counter grouped by subsystem, derived health signals (crisp vs soft ratio, cache hit
rates, scroll jank, cold vs warm shows), and the notable events from the log. stdlib-only; run it on
a `.zip` or an already-unzipped directory:

    uv run overlay/tools/trace_report.py ~/.local/share/saitenka/reports/saitenka-report-*.zip
    uv run overlay/tools/trace_report.py --spans --log /tmp/unzipped-report-dir
"""

from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


def _pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..1) of an already-sorted list; 0.0 when empty."""
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


# --- loading -------------------------------------------------------------------------------------


def _read_member(src: Path, name: str) -> str | None:
    """Read `name` from a report that is either a .zip or an unzipped directory. None if absent."""
    if src.is_dir():
        p = src / name
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    with zipfile.ZipFile(src) as z:
        cand = [n for n in z.namelist() if n.endswith(name) or n == name]
        if not cand:
            return None
        return z.read(cand[0]).decode("utf-8", errors="replace")


def _load_trace(src: Path) -> list[dict]:
    raw = _read_member(src, "telemetry/trace.json") or _read_member(src, "trace.json")
    if raw is None:
        return []
    doc = json.loads(raw)
    return doc.get("traceEvents", doc) if isinstance(doc, dict) else doc


def _load_log(src: Path) -> list[dict]:
    raw = _read_member(src, "overlay.log")
    if raw is None:
        return []
    out = []
    for line in raw.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# --- span analysis -------------------------------------------------------------------------------


@dataclass
class SpanStat:
    name: str
    durs_ms: list[float] = field(default_factory=list)
    self_ms: float = 0.0
    cpu_ms: float = 0.0

    def row(self) -> tuple:
        s = sorted(self.durs_ms)
        return (
            self.name,
            len(s),
            sum(s),
            self.self_ms,
            self.cpu_ms,
            _pct(s, 0.5),
            _pct(s, 0.95),
            _pct(s, 0.99),
            s[-1] if s else 0.0,
        )


def _self_times(spans: list[dict]) -> dict[str, float]:
    """Self-time per span NAME: dur minus the time covered by strictly-nested children. The tracer
    emits no parent id, so nesting is inferred by containment within one thread (tid) — a span whose
    [ts, ts+dur) sits inside another's on the same thread is that other's child. Overlapping siblings
    on a thread are summed but clamped to the parent, so self-time never goes negative."""
    by_tid: dict[int, list[dict]] = defaultdict(list)
    for s in spans:
        by_tid[s.get("tid", 0)].append(s)
    self_ms: dict[str, float] = defaultdict(float)
    for group in by_tid.values():
        # Sort by start, then by widest-first so a parent is seen before its children.
        group.sort(key=lambda e: (e["ts"], -e.get("dur", 0.0)))
        for i, sp in enumerate(group):
            start, end = sp["ts"], sp["ts"] + sp.get("dur", 0.0)
            covered = 0.0
            cursor = start
            for child in group[i + 1 :]:
                cs = child["ts"]
                if cs >= end:
                    break
                ce = min(child["ts"] + child.get("dur", 0.0), end)
                if cs >= cursor:  # a direct child (not already inside a counted one)
                    covered += ce - cs
                    cursor = ce
            self_ms[sp["name"]] += max(0.0, (end - start) - covered) / 1000.0
    return self_ms


def span_stats(events: list[dict]) -> list[SpanStat]:
    spans = [e for e in events if e.get("ph") == "X"]
    self_ms = _self_times(spans)
    stats: dict[str, SpanStat] = {}
    for sp in spans:
        st = stats.setdefault(sp["name"], SpanStat(sp["name"]))
        st.durs_ms.append(sp.get("dur", 0.0) / 1000.0)
        st.cpu_ms += float(sp.get("args", {}).get("cpu_ms", 0.0))
    for name, st in stats.items():
        st.self_ms = self_ms.get(name, 0.0)
    return sorted(stats.values(), key=lambda s: sum(s.durs_ms), reverse=True)


def attr_breakdowns(events: list[dict]) -> dict[str, Counter]:
    """Low-cardinality span attribute value distributions (tooltip_show cold=…, render kind=…)."""
    out: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        if e.get("ph") != "X":
            continue
        for k, v in e.get("args", {}).items():
            if k in {"span_id", "trace_id", "cpu_ms"}:
                continue
            out[f"{e['name']}.{k}"][str(v)] += 1
    return out


def _first(spans: list[dict], name: str, pred=None) -> dict | None:
    """Earliest complete span called ``name`` (optionally matching ``pred(args)``), by start time."""
    hits = [s for s in spans if s["name"] == name and (pred is None or pred(s.get("args", {})))]
    return min(hits, key=lambda s: s["ts"]) if hits else None


def first_paints(events: list[dict]) -> list[tuple[str, dict | None, str]]:
    """The user-perceived 'first X paint' latencies, timestamped from session start (the earliest span).
    Each row is (label, span_or_None, note). ``tooltip_show`` is the end-to-end base hover→drawn; nested
    and clicked paints have no ``tooltip_show``, so they're taken from the ``tip_compose`` ``kind``
    attribute (absent in bundles from before that marker → the note says so, not a false 'never')."""
    spans = [e for e in events if e.get("ph") == "X"]
    kinds = {s.get("args", {}).get("kind") for s in spans if s["name"] == "tip_compose"}
    tagged = any(k is not None for k in kinds)
    untagged = "" if tagged else "  (tip_compose has no `kind` — bundle predates the marker)"
    return [
        (
            "first subtitle cue paint",
            _first(spans, "subtitle_render") or _first(spans, "cue_redraw"),
            "",
        ),
        ("first tooltip paint (base)", _first(spans, "tooltip_show"), ""),
        (
            "first nested tooltip paint",
            _first(spans, "tip_compose", lambda a: a.get("kind") == "nested"),
            untagged,
        ),
        (
            "first clicked tooltip paint",
            _first(spans, "tip_compose", lambda a: a.get("kind") == "clicked"),
            untagged,
        ),
    ]


def final_counters(events: list[dict]) -> dict[str, float]:
    """Last sampled value of each pull-based counter (ph == C)."""
    out: dict[str, float] = {}
    for e in events:
        if e.get("ph") == "C":
            out[e["name"]] = e["args"]["value"]
    return out


# --- printing ------------------------------------------------------------------------------------


def _hit_rate(c: dict[str, float], base: str) -> str:
    h, m = c.get(f"{base}.hits", 0.0), c.get(f"{base}.misses", 0.0)
    tot = h + m
    return f"{h / tot * 100:5.1f}% ({int(h)}/{int(tot)})" if tot else "   n/a"


def print_report(
    src: Path, events: list[dict], log: list[dict], *, want_spans: bool, want_log: bool
):
    counters = final_counters(events)
    spans = span_stats(events)
    print(f"# saitenka trace report — {src.name}")
    print(
        f"  {len([e for e in events if e.get('ph') == 'X'])} spans, "
        f"{len({e['name'] for e in events if e.get('ph') == 'C'})} counters, {len(log)} log lines\n"
    )

    print("## diagnostics")
    swaps = counters.get("crisp.swaps", 0.0)
    soft = next(
        (sum(1 for e in events if e.get("ph") == "X" and e["name"] == "tip_compose") for _ in [0]),
        0,
    )
    print(f"  crisp swaps (native composites)      {int(swaps)}")
    print(
        f"  soft tip_compose (upscaled blits)    {soft}"
        f"   → {'mostly crisp' if swaps > soft else 'MOSTLY SOFT — check keyless/navigated views'}"
    )
    breaks = attr_breakdowns(events)
    reasons = breaks.get("tip_compose.soft_reason")
    if (
        reasons
    ):  # WHY each soft blit fell back — stale_scale = OSD scale jittered + orphaned the panel
        print(f"  soft-fallback reasons                {dict(reasons.most_common())}")
    scales = Counter()
    for k in ("scroll_frame.scale", "tip_compose.scale", "crisp_render.scale"):
        scales.update(breaks.get(k, Counter()))
    if (
        scales
    ):  # distinct display scales seen mid-session — >1 ~equal value ⇒ jitter re-keying crisp
        top = scales.most_common(8)
        print(f"  display scales seen ({len(scales)} distinct)  {dict(top)}")
    for base in (
        "render_cache",
        "mask_atlas",
        "panel_cache",
        "block_cache",
        "bgra_memo",
        "dict_cache",
    ):
        if any(k.startswith(base + ".") for k in counters):
            print(f"  {base:<16} hit-rate           {_hit_rate(counters, base)}")
    jank = next((s for s in spans if s.name == "scroll_frame"), None)
    if jank:
        _, n, _, _, _, p50, p95, p99, mx = jank.row()
        print(
            f"  scroll_frame ms                      p50={p50:.1f} p95={p95:.1f} p99={p99:.1f} max={mx:.1f} (n={n})"
        )
    shows = attr_breakdowns(events).get("tooltip_show.cold")
    if shows:
        print(f"  tooltip_show cold/warm               {dict(shows)}")
    print()

    x_spans = [e for e in events if e.get("ph") == "X"]
    if x_spans:
        t0 = min(s["ts"] for s in x_spans)
        print("## first paints (t+ from session start, ms)")
        for label, sp, note in first_paints(events):
            if sp is None:
                print(f"  {label:<30} —{note or '  (none this session)'}")
                continue
            a = sp.get("args", {})
            tags = " ".join(
                f"{k}={a[k]}" for k in ("cold", "kind", "soft_reason", "scale") if k in a
            )
            print(
                f"  {label:<30} t+{(sp['ts'] - t0) / 1000:8.1f}  "
                f"dur={sp.get('dur', 0.0) / 1000:6.1f}ms  {tags}"
            )
        kinds = breaks.get("tip_compose.kind")
        if kinds:  # per-kind paint counts once the marker is present
            print(f"  tip_compose by kind                  {dict(kinds.most_common())}")
        print()

    probe_keys = sorted(k for k in breaks if k.startswith("osd_probe."))
    if probe_keys:
        n_probe = sum(breaks[probe_keys[0]].values())  # each probe emits every attr once
        print(f"## display sources — why the scale jumps ({n_probe} osd probes)")
        print(
            "  a STABLE source has 1 value; the JITTERY one(s) drive the scale wobble → key scale off a stable source"
        )
        for k in probe_keys:
            dist = breaks[k]
            src = k.split(".", 1)[1]
            tag = "  <- stable" if len(dist) == 1 else ("  <- JITTERS" if len(dist) > 2 else "")
            print(f"  {src:<14}{len(dist):>2} distinct  {dict(dist.most_common(6))}{tag}")
        print()

    print("## spans by total time (ms)")
    print(
        f"  {'name':<22}{'n':>5}{'total':>9}{'self':>9}{'cpu':>8}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}"
    )
    for st in spans:
        name, n, total, self_ms, cpu, p50, p95, p99, mx = st.row()
        print(
            f"  {name:<22}{n:>5}{total:>9.1f}{self_ms:>9.1f}{cpu:>8.1f}{p50:>8.1f}{p95:>8.1f}{p99:>8.1f}{mx:>8.1f}"
        )
    print()

    print("## counters (final)")
    for name in sorted(counters):
        print(f"  {counters[name]:>14.0f}  {name}")
    print()

    if want_spans:
        print("## span attribute breakdowns")
        for key, dist in sorted(attr_breakdowns(events).items()):
            print(f"  {key:<28} {dict(dist.most_common())}")
        print()

    if want_log:
        print("## notable log events (last session)")
        keys = (
            "tooltip shown",
            "click at",
            "navigat",
            "back",
            "using the default layout",
            "error",
            "warn",
            "seek",
            "teardown",
        )
        last_seed = max(
            (r["timestamp"] for r in log if "observing mpv props" in r.get("event", "")), default=""
        )
        for r in log:
            if last_seed and r.get("timestamp", "") < last_seed:
                continue
            e = r.get("event", "")
            if any(k in e for k in keys):
                print(f"  {r.get('timestamp', '')[11:19]} {r.get('level', ''):<5} {e[:150]}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Distil a saitenka report bundle into a perf/diagnostics report."
    )
    ap.add_argument(
        "report", type=Path, help="path to a saitenka-report-*.zip or an unzipped directory"
    )
    ap.add_argument("--spans", action="store_true", help="also print per-attribute span breakdowns")
    ap.add_argument("--log", action="store_true", help="also print notable overlay.log events")
    args = ap.parse_args()
    events = _load_trace(args.report)
    if not events:
        raise SystemExit(f"no telemetry/trace.json found in {args.report}")
    log = _load_log(args.report) if args.log else []
    print_report(args.report, events, log, want_spans=args.spans, want_log=args.log)


if __name__ == "__main__":
    main()
