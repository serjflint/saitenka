"""How long each cue waited for its color, read out of a report bundle's trace.

The wait a viewer sees runs from mpv putting a cue up to our color landing on it. No instrument
measures that interval, and one was nearly added — a histogram plus three fields of renderer state —
before it was clear the trace already holds both ends. `subtitle_draw` fires per draw carrying
`measured_boxes` and a `cue` handle, so the answer is a group-by and a subtraction:

    first draw of a cue           -> the cue is on screen
    first of that cue with boxes  -> the color is on screen

Deriving it here rather than recording it keeps the renderer stateless and leaves every other
interval in the same trace derivable later, instead of only the one somebody thought to instrument.
What it costs: the number needs a bundle. `saitenka doctor` cannot print it live.

    uv run python tools/report_color_latency.py <report.zip>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import zipfile
from pathlib import Path


def draws(trace: dict) -> list[dict]:
    return [
        {"ts": event["ts"] / 1000.0, **(event.get("args") or {})}
        for event in trace.get("traceEvents", ())
        if event.get("ph") == "X" and event.get("name") == "subtitle_draw"
    ]


def waits(spans: list[dict]) -> tuple[list[float], list[str], list[str]]:
    """Per cue: the wait in ms, or the cue's handle when its color never arrived.

    A cue that is never colored contributes NO duration, so a summary built only from the durations
    reports the session as fast by omitting exactly the failures. It is returned separately.
    """
    by_cue: dict[str, list[dict]] = {}
    for span in spans:
        cue = span.get("cue")
        if cue is not None and span.get("path") == "native":
            by_cue.setdefault(cue, []).append(span)
    measured: list[float] = []
    never: list[str] = []
    for cue, group in by_cue.items():
        group.sort(key=lambda item: item["ts"])
        colored = next((item for item in group if item.get("measured_boxes")), None)
        if colored is None:
            never.append(cue)
        else:
            measured.append(colored["ts"] - group[0]["ts"])
    return measured, never, list(by_cue)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="a saitenka report .zip")
    args = parser.parse_args(argv)

    with zipfile.ZipFile(args.report) as bundle:
        trace = json.loads(bundle.read("telemetry/trace.json"))
    spans = draws(trace)
    if not spans:
        print("no subtitle_draw spans — the bundle predates them, or telemetry was off")
        return 1
    if not any("cue" in span for span in spans):
        print("subtitle_draw spans carry no cue handle — the bundle predates it")
        return 1

    measured, never, cues = waits(spans)
    print(f"{len(cues)} native cues, {len(measured)} colored, {len(never)} never colored")
    if measured:
        ordered = sorted(measured)
        print(
            f"  wait to color:  p50 {statistics.median(ordered):7.1f} ms"
            f"   p95 {ordered[int(len(ordered) * 0.95)]:7.1f} ms"
            f"   max {ordered[-1]:7.1f} ms"
        )
    if never:
        # The headline the durations cannot carry: a cue nobody ever colored is not a slow cue.
        print(
            f"  NEVER colored:  {len(never)} of {len(cues)} ({100 * len(never) / len(cues):.0f}%)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
