"""How long each cue waited for its color, read out of a report bundle's trace.

The wait a viewer sees runs from mpv putting a cue up to our color landing on it. No instrument
measures that interval, and one was nearly added — a histogram plus three fields of renderer state —
before it was clear the trace already holds both ends. `subtitle_draw` fires per draw carrying
`measured_boxes` and a `cue` handle, so the answer is a group-by and a subtraction:

    first draw of an appearance           -> the cue is on screen
    first of that appearance with boxes   -> the color is on screen

Deriving it here rather than recording it keeps the renderer stateless and leaves every other
interval in the same trace derivable later, instead of only the one somebody thought to instrument.
What it costs: the number needs a bundle. `saitenka doctor` cannot print it live.

    uv run python tools/report_color_latency.py <report.zip>
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path


def draws(trace: dict) -> list[dict]:
    return [
        {"ts": event["ts"] / 1000.0, **(event.get("args") or {})}
        for event in trace.get("traceEvents", ())
        if event.get("ph") == "X" and event.get("name") == "subtitle_draw"
    ]


def decisions(trace: dict) -> list[dict]:
    """`subtitle_geometry_decision` spans, which carry how many tokens were owed a box."""
    return [
        {"ts": event["ts"] / 1000.0, **(event.get("args") or {})}
        for event in trace.get("traceEvents", ())
        if event.get("ph") == "X" and event.get("name") == "subtitle_geometry_decision"
    ]


def caused_by(trace: dict, name: str) -> dict[str, str]:
    """For each span of ``name``, the nearest ancestor's name — why it happened, not when.

    The trace has carried `parent_id` all along, with every parent resolving, and this readout
    ignored it for its whole life: it correlated by timestamp window, which is the technique that
    produced three confident wrong mechanisms in one session. The edge that settled the `犬` bug was
    one lookup:

        subtitle_draw <- cue_redraw <- sub_seek           the cue arrived, we drew it
        subtitle_draw <- subtitle_geometry_apply          the GEOMETRY caused this draw

    A draw whose parent is the apply is a redraw the geometry side triggered, which is exactly the
    moment the cue side may have moved on. No amount of adjacency says that; the edge does.
    """
    spans = {
        (event.get("args") or {}).get("span_id"): event
        for event in trace.get("traceEvents", ())
        if event.get("ph") == "X"
    }
    causes: dict[str, str] = {}
    for span_id, event in spans.items():
        if event.get("name") != name or span_id is None:
            continue
        parent = spans.get((event.get("args") or {}).get("parent_id"))
        causes[span_id] = "root" if parent is None else parent["name"]
    return causes


#: An appearance this short is gone before anyone reads it, so its color never arriving is not a
#: defect — mpv shows the line for less time than a slow blink. Reported apart rather than dropped:
#: still evidence, just not evidence of the thing being chased.
GLIMPSE_MS = 250.0


def settling(settled: list[dict]) -> list[float]:
    """How long each provisional draw sat on screen before the cue's own rows arrived.

    This is the interval a viewer actually experiences, and no per-cue grouping can recover it: a
    `sub-seek` redraws optimistically with text that has not settled, so the provisional draw and
    the settled one carry *different* text and hash to different cue handles. Measured per
    appearance, each half looks fast — the provisional one is a sub-100 ms flash, the settled one
    colors in 0.0 ms — and the wait between them belongs to neither.

    Derived instead from the decision either side of it, which needs no grouping at all:

        pending / subtitle-observation-pending  -> text is up, its rows are not
        the next `ready`                        -> the rows landed, color goes on
    """
    waits = []
    for index, span in enumerate(settled):
        if span.get("reason") != "subtitle-observation-pending":
            continue
        ready = next(
            (later["ts"] for later in settled[index + 1 :] if later.get("outcome") == "ready"), None
        )
        if ready is not None:
            waits.append(ready - span["ts"])
    return waits


@dataclass(frozen=True)
class Appearance:
    """One time a cue was on screen. NOT one cue handle — see :func:`appearances`."""

    cue: str
    start: float
    #: The next appearance's first draw. `None` for the last one, which nothing bounds.
    end: float | None
    draws: int
    #: Milliseconds from the first draw to the first carrying boxes; `None` if none ever did.
    wait: float | None
    #: Tokens the settled geometry decision owed a box. `None` when no decision was recorded in
    #: this window, which is not the same as zero and must not be read as one.
    eligible: int | None
    #: Draws that carried boxes with no tokens to put them on — geometry published against a cue
    #: that cannot use it. Never a paint; always a defect.
    orphan_boxes: int

    @property
    def held(self) -> float:
        return float("inf") if self.end is None else self.end - self.start

    @property
    def owed_color(self) -> bool:
        """False only when the geometry positively settled on nothing to paint."""
        return self.eligible != 0


def appearances(spans: list[dict], settled: list[dict] | None = None) -> list[Appearance]:
    """Split native draws into on-screen appearances, in order.

    The `cue` handle is a digest of the cue's *content*, so a line that recurs — a two-character
    interjection, a repeated name — comes back under the handle it had 17 seconds ago. Grouping by
    handle alone welds those into one appearance and computes a screen time spanning the gap.

    An appearance therefore ends when a *different* cue is drawn: mpv shows one at a time, so that
    is exact and needs no gap threshold to tune.
    """
    ordered = sorted(
        (span for span in spans if span.get("path") == "native"), key=lambda span: span["ts"]
    )
    runs: list[list[dict]] = []
    for span in ordered:
        if runs and runs[-1][0].get("cue") == span.get("cue"):
            runs[-1].append(span)
        else:
            runs.append([span])
    settled = settled or []
    result: list[Appearance] = []
    for index, run in enumerate(runs):
        start = run[0]["ts"]
        end = runs[index + 1][0]["ts"] if index + 1 < len(runs) else None
        # Boxes AND tokens to put them on. A draw carrying boxes with zero tokens is geometry
        # published against a cue that cannot use it — the field showed three boxes belonging to
        # one cue filed against another — and scoring that as colored reports a paint that never
        # happened. Counted separately below rather than dropped.
        colored = next(
            (span for span in run if span.get("measured_boxes") and span.get("tokens")), None
        )
        result.append(
            Appearance(
                cue=run[0].get("cue", ""),
                start=start,
                end=end,
                draws=len(run),
                wait=None if colored is None else colored["ts"] - start,
                eligible=_eligible_in(settled, start, end, run[0].get("cue")),
                orphan_boxes=sum(
                    1 for span in run if span.get("measured_boxes") and not span.get("tokens")
                ),
            )
        )
    return result


def _eligible_in(
    settled: list[dict], start: float, end: float | None, cue: str | None = None
) -> int | None:
    """How many tokens the last *settled* decision for this appearance owed a box.

    Matched on the cue handle when the bundle carries one, and only then narrowed by the window --
    the handle identifies content, so a repeated line needs both. A bundle predating the handle
    falls back to the window alone, which is what this did for every appearance and is why a
    decision belonging to an adjacent cue could be read as this one's.

    Only a `ready` decision answers: a `pending` one is the question, not the answer, and reading its
    zero as "nothing to paint" would file every slow cue as one that wanted no color.
    """
    ready = [
        span
        for span in settled
        if span.get("outcome") == "ready"
        and start <= span["ts"]
        and (end is None or span["ts"] < end)
        and (cue is None or span.get("cue") is None or span.get("cue") == cue)
    ]
    return ready[-1].get("eligible_tokens") if ready else None


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

    settled = decisions(trace)
    shown = appearances(spans, settled)
    # An appearance owed no box is not a failure to color it: it leaves the denominator rather than
    # moving to the numerator's other side.
    owed = [item for item in shown if item.owed_color]
    unpaintable = len(shown) - len(owed)
    measured = sorted(item.wait for item in owed if item.wait is not None)
    never = [item for item in owed if item.wait is None]

    print(f"{len(shown)} native appearances, {len(owed)} owed color, {len(measured)} colored")
    if measured:
        print(
            f"  wait to color:  p50 {statistics.median(measured):7.1f} ms"
            f"   p95 {measured[int(len(measured) * 0.95)]:7.1f} ms"
            f"   max {measured[-1]:7.1f} ms"
        )
    if unpaintable:
        print(f"  no color owed:  {unpaintable} (geometry settled on 0 eligible tokens)")
    orphans = sum(item.orphan_boxes for item in shown)
    if orphans:
        print(f"  ORPHAN boxes:   {orphans} draw(s) carried boxes with no tokens to put them on")
    causes = collections.Counter(caused_by(trace, "subtitle_draw").values())
    if causes:
        # Why each draw happened, from `parent_id` rather than from what preceded it.
        print("  draws caused by: " + ", ".join(f"{name} x{n}" for name, n in causes.most_common()))
    unsettled = settling(settled)
    if unsettled:
        # The one a viewer complains about, and the one the per-appearance durations cannot carry.
        ordered = sorted(unsettled)
        print(
            f"  text before rows: {len(ordered)} draw(s)"
            f"   p50 {statistics.median(ordered):6.1f} ms   max {ordered[-1]:6.1f} ms"
        )
    if never:
        missed = [item for item in never if item.held >= GLIMPSE_MS]
        glimpsed = len(never) - len(missed)
        share = f"{100 * len(missed) / len(owed):.0f}%" if owed else "n/a"
        print(f"  NEVER colored:  {len(missed)} of {len(owed)} ({share})")
        for item in sorted(missed, key=lambda item: -item.held):
            held = "to end of session" if item.end is None else f"{item.held:.0f} ms on screen"
            # No decision in the window means the reason is unrecorded, not that none existed —
            # the argument for carrying eligible_tokens on the draw itself.
            why = "no geometry decision recorded" if item.eligible is None else "geometry settled"
            print(f"      {item.cue}  {held}, {item.draws} draw(s), {why}")
        if glimpsed:
            print(f"  (+{glimpsed} gone in under {GLIMPSE_MS:.0f} ms — too brief to read)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
