"""How long each cue waited for its color, derived from a report bundle's trace.

    first draw of an appearance           -> the cue is on screen
    first of that appearance with boxes   -> the color is on screen

Derived rather than recorded, which keeps the renderer stateless and leaves the trace's other
intervals derivable too. The cost: it needs a bundle, so `saitenka doctor` cannot print it live.

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


def from_records(records: list[dict], name: str) -> list[dict]:
    """The shape :func:`draws` yields, from in-process ``traced`` records instead of a bundle.

    Lets a test judge a driven timeline with the readout the field goes through, so the instrument
    is gated rather than only trusted. A recorded span carries no clock, so the ordinal stands in
    for ``ts``: it orders and it groups, which is all :func:`appearances` needs — a *duration* read
    off one would be a fiction.
    """
    return [
        {"ts": float(index), **(record.get("attrs") or {})}
        for index, record in enumerate(records)
        if record.get("name") == name
    ]


def decisions(trace: dict) -> list[dict]:
    """`subtitle_geometry_decision` spans, which carry how many tokens were owed a box."""
    return [
        {"ts": event["ts"] / 1000.0, **(event.get("args") or {})}
        for event in trace.get("traceEvents", ())
        if event.get("ph") == "X" and event.get("name") == "subtitle_geometry_decision"
    ]


def caused_by(trace: dict, name: str) -> dict[str, str]:
    """For each span of ``name``, its parent's name — why it happened, not what preceded it.

        subtitle_draw <- cue_redraw <- sub_seek     the cue arrived, we drew it
        subtitle_draw <- subtitle_geometry_apply    the geometry side triggered this redraw

    The second is where the cue side may have moved on underneath. Adjacency cannot distinguish
    them; the edge can.
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


def seek_waits(trace: dict) -> list[tuple[float | None, str]]:
    """Per navigation, how long until color was on screen — across cue identity, not within it.

    A seek draws a navigation *hint* from the cue index, and mpv's own `sub-text` follows tens of
    milliseconds later under a different digest. Ending the measurement at that change reports the
    hint's 0 ms and calls the rest somebody else's appearance, so the wait a viewer actually sits
    through — the whole point of this file — falls in the gap between two green numbers. It read
    `p50 0.0 ms` against a measured 48.6.

    Bounded by the next navigation, so a seek whose color never arrives stays `None` instead of
    borrowing the next one's.
    """
    events = sorted(
        (
            event
            for event in trace.get("traceEvents", ())
            if event.get("ph") == "X" and event.get("name") in {"sub_nav_identity", "subtitle_draw"}
        ),
        key=lambda event: event["ts"],
    )
    result: list[tuple[float | None, str]] = []
    started: float | None = None
    for event in events:
        args = event.get("args") or {}
        if event["name"] == "sub_nav_identity":
            started = event["ts"] / 1000.0
            result.append((None, ""))
            continue
        if started is None or args.get("path") != "native" or result[-1][0] is not None:
            continue
        if args.get("measured_boxes") and args.get("tokens"):
            result[-1] = (event["ts"] / 1000.0 - started, args.get("cue", ""))
    return result


#: Below this an appearance is gone before anyone reads it, so a missing color is not a defect.
#: Reported apart rather than dropped — still evidence, just not of the thing being chased.
GLIMPSE_MS = 250.0


def settling(settled: list[dict]) -> list[float]:
    """How long each provisional draw sat on screen before the cue's own rows arrived.

    No per-cue grouping recovers this: a `sub-seek` redraws with unsettled text, so the provisional
    draw and the settled one hash to different handles and each half looks fast alone. Taken from
    the decisions either side instead:

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
    #: Tokens the settled decision owed a box. `None` when none was recorded — not the same as zero.
    eligible: int | None
    #: Draws carrying boxes with no tokens to put them on. Never a paint; always a defect.
    orphan_boxes: int
    #: Tokens the DRAW itself said it owed color. Preferred over `eligible`: it rides on the draw,
    #: so it cannot be missing the way a decision span can, and "no geometry decision recorded" was
    #: this readout's most common verdict on the appearances nobody could explain.
    owed: int | None = None
    #: Draws that lost color this cue already had. The wait takes the FIRST colored draw and stops,
    #: so a mid-cue drop is invisible to it — three were found by hand and none by this.
    lost_color: int = 0

    @property
    def held(self) -> float:
        return float("inf") if self.end is None else self.end - self.start

    @property
    def owed_color(self) -> bool:
        """False when the cue positively owed no color. The draw's own count answers first: it is
        always present, where a decision span often is not."""
        if self.owed is not None:
            return self.owed != 0
        return self.eligible != 0


def appearances(spans: list[dict], settled: list[dict] | None = None) -> list[Appearance]:
    """Split native draws into on-screen appearances, in order.

    The handle digests *content*, so a recurring line returns under the one it had earlier and
    grouping by handle welds separate showings together. An appearance ends when a different cue is
    drawn: mpv shows one at a time, so that is exact and needs no gap threshold.
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
                owed=next(
                    (span["owed_color"] for span in run if span.get("owed_color") is not None), None
                ),
                lost_color=sum(1 for span in run if span.get("lost_color")),
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
    lost = sum(item.lost_color for item in shown)
    if lost:
        print(f"  LOST color:     {lost} draw(s) dropped color the same cue already had")
    orphans = sum(item.orphan_boxes for item in shown)
    if orphans:
        print(f"  ORPHAN boxes:   {orphans} draw(s) carried boxes with no tokens to put them on")
    navigated = seek_waits(trace)
    if navigated:
        landed = [wait for wait, _cue in navigated if wait is not None]
        blank = len(navigated) - len(landed)
        if landed:
            ranked = sorted(landed)
            # p95 as well as p50, because they answer different questions and this readout was
            # quoted on the median alone while a twentieth of its seeks cost fifty times it. On a
            # session's worth of seeks p95 IS the max — `n` is printed so that is visible rather
            # than implied.
            print(
                f"  after a seek:   n={len(landed)}"
                f"   p50 {statistics.median(ranked):6.1f} ms"
                f"   p95 {ranked[min(int(len(ranked) * 0.95), len(ranked) - 1)]:6.1f} ms"
                f"   max {ranked[-1]:6.1f} ms"
                + (f"   (+{blank} never colored before the next seek)" if blank else "")
            )
    refused = collections.Counter(
        span.get("reason") for span in settled if span.get("outcome") == "pending"
    )
    if refused:
        # What splits a 2 ms seek from a 50 ms one. `subtitle-hint-text-mismatch` is the expensive
        # one: the hint cannot be painted, so color waits for mpv's own `sub-text` to arrive.
        print("  refused: " + ", ".join(f"{name} x{n}" for name, n in refused.most_common()))
    lane = collections.Counter(
        (event.get("args") or {}).get("outcome")
        for event in trace.get("traceEvents", ())
        if event.get("ph") == "X" and event.get("name") == "subtitle_geometry_lane"
    )
    if lane:
        # A cue re-requested at a different instant within itself misses the result cache and
        # re-renders, so `rendering` outnumbering `cached` across repeat visits is the signature.
        print("  lane: " + ", ".join(f"{name} x{n}" for name, n in lane.most_common()))
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
