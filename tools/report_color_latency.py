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


#: A cue this short is gone before anyone reads it, so its color never arriving is not a defect —
#: mpv shows the line for less time than a slow blink. Measured against the gap to the NEXT cue,
#: which is how long this one held the screen. Cues below this are reported apart rather than
#: dropped: they are still evidence, just not evidence of the thing being chased.
GLIMPSE_MS = 250.0


def waits(spans: list[dict]) -> tuple[list[float], list[tuple[str, float]], list[str]]:
    """Per cue: the wait in ms, or the cue's handle and screen time when its color never arrived.

    A cue that is never colored contributes NO duration, so a summary built only from the durations
    reports the session as fast by omitting exactly the failures. It is returned separately.

    Screen time comes from the gap to the next cue's first draw. Without it the headline overstates:
    a session read 6 of 18 cues uncolored, and 4 of those six had held the screen for 60-92 ms.
    """
    by_cue: dict[str, list[dict]] = {}
    for span in spans:
        cue = span.get("cue")
        if cue is not None and span.get("path") == "native":
            by_cue.setdefault(cue, []).append(span)
    for group in by_cue.values():
        group.sort(key=lambda item: item["ts"])
    order = sorted(by_cue, key=lambda cue: by_cue[cue][0]["ts"])
    measured: list[float] = []
    never: list[tuple[str, float]] = []
    for index, cue in enumerate(order):
        group = by_cue[cue]
        colored = next((item for item in group if item.get("measured_boxes")), None)
        if colored is not None:
            measured.append(colored["ts"] - group[0]["ts"])
            continue
        # The last cue has no successor to bound it; treat it as long-lived rather than invent one.
        following = by_cue[order[index + 1]][0]["ts"] if index + 1 < len(order) else None
        held = float("inf") if following is None else following - group[0]["ts"]
        never.append((cue, held))
    return measured, never, order


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
        # Split by screen time, because counting a 60 ms flash beside a cue held for half a second
        # reports one number for two different things and inflates the one that matters.
        missed = [item for item in never if item[1] >= GLIMPSE_MS]
        glimpsed = len(never) - len(missed)
        print(
            f"  NEVER colored:  {len(missed)} of {len(cues)} ({100 * len(missed) / len(cues):.0f}%)"
        )
        for cue, held in sorted(missed, key=lambda item: -item[1]):
            held_text = "to end of session" if held == float("inf") else f"{held:.0f} ms on screen"
            print(f"      {cue}  {held_text}")
        if glimpsed:
            print(f"  (+{glimpsed} cue(s) gone in under {GLIMPSE_MS:.0f} ms — too brief to read)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
