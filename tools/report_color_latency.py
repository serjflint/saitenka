"""Cue arrival to mpv acknowledgment, with missing evidence retained in the denominator.

Neither a draw nor an IPC acknowledgment establishes physical display presentation.
Older draw-based helpers remain compatibility diagnostics, not acknowledgment latency.

    uv run python tools/report_color_latency.py <report.zip>
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from saitenka.app.color_evidence import safe_color_metrics
from saitenka.app.report_reader import read_member, trace_evidence
from saitenka.app.timed_osd_evidence import safe_history


def metadata_timed_history(path: Path) -> list[dict]:
    try:
        raw = read_member(path, "diagnostics/envelope.json")
        envelope = json.loads(raw) if raw else {}
        return [
            safe_history(owner.get("timed_osd"))
            for owner in envelope.get("effective_runtime_configuration", {}).get("owners", [])
        ]
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def metadata_color_metrics(path: Path) -> dict:
    if path.suffix != ".zip":
        return {}
    try:
        raw = read_member(path, "diagnostics/envelope.json")
        envelope = json.loads(raw) if raw is not None else {}
        color = envelope.get("operation_health", {}).get("subtitle_color", {})
        return safe_color_metrics(color.get("metrics")) if isinstance(color, dict) else {}
    except (OSError, ValueError, AttributeError, RecursionError):
        return {}


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


def color_on_screen(trace: dict) -> list[tuple[str, float]]:
    """Legacy heuristic mixing draw and write timestamps; not display or acknowledgment latency.

    Both ends of the older reading were ours rather than the viewer's, and both were early:

      start  our first draw          -> the cue ARRIVED. In native-visible mode mpv puts the text
                                        up itself; our draw is not what makes the line appear, so
                                        anchoring on it skips however long the line was already
                                        showing plain (1.8-6.7 ms here).
      end    our draw call returning -> mpv ACKNOWLEDGING the write. The draw hands off a payload;
                                        the pixels arrive when mpv has parsed and composited it,
                                        which is 2-34 ms later and was invisible until
                                        `surface_write` measured it.

    Between them they reported 0.0 ms for cues that took 4-37 ms. A p50 of zero is what prompted
    the maintainer to say the number could not be right, and it could not.

    The search is bounded at the next arrival of a *different* cue. A digest is not unique across a
    session — the same line comes back on a seek — so an unbounded scan pairs an arrival that never
    colored with a draw belonging to a later appearance of the same text. That reported 2.7 s of
    "wait" across an interval in which six other cues came and went.
    """
    arrivals = sorted(
        (
            event
            for event in trace.get("traceEvents", ())
            if event.get("ph") == "X"
            and event.get("name") in {"cue_reconcile", "sub_seek"}
            and (event.get("args") or {}).get("cue")
        ),
        key=lambda event: event["ts"],
    )
    spans = sorted(
        (event for event in trace.get("traceEvents", ()) if event.get("ph") == "X"),
        key=lambda event: event["ts"],
    )
    result: list[tuple[str, float]] = []
    for index, arrival in enumerate(arrivals):
        start = arrival["ts"] / 1000.0
        cue = str((arrival.get("args") or {}).get("cue"))
        horizon = next(
            (
                later["ts"] / 1000.0
                for later in arrivals[index + 1 :]
                if (later.get("args") or {}).get("cue") != cue
            ),
            float("inf"),
        )
        after = [span for span in spans if start <= span["ts"] / 1000.0 < horizon]
        painted = next(
            (
                span
                for span in after
                if span.get("name") == "subtitle_draw"
                and (span.get("args") or {}).get("cue") == cue
                and (span.get("args") or {}).get("measured_boxes")
                and (span.get("args") or {}).get("tokens")
            ),
            None,
        )
        if painted is None:
            continue
        acked = next(
            (
                span
                for span in after
                if span["ts"] >= painted["ts"]
                and span.get("name") == "surface_write"
                and (span.get("args") or {}).get("slot") == _COLOR_SLOT
                and (span.get("args") or {}).get("events")
            ),
            None,
        )
        result.append((cue, (acked or painted)["ts"] / 1000.0 - start))
    return result


def color_arrivals(trace: dict) -> list[dict]:
    """Occurrence-qualified overprint acknowledgments; no draw fallback in latency statistics."""
    if any(
        event.get("name") in {"subtitle_color_arrival", "subtitle_color_outcome"}
        for event in trace.get("traceEvents", ())
    ):
        return occurrence_outcomes(trace)
    spans = sorted(
        (event for event in trace.get("traceEvents", []) if event.get("ph") == "X"),
        key=lambda event: event["ts"],
    )
    arrivals = [event for event in spans if event.get("name") == "cue_reconcile"]
    results = []
    for index, arrival in enumerate(arrivals):
        args = arrival.get("args") or {}
        revision = args.get("cue_revision")
        row = {
            "cue": args.get("cue"),
            "cue_revision": revision,
            "endpoint": "mpv-acknowledgment",
            "wait_ms": None,
            "status": "unknown-correlation",
        }
        if revision is not None:
            horizon = arrivals[index + 1]["ts"] if index + 1 < len(arrivals) else float("inf")
            candidates = [
                event
                for event in spans
                if arrival["ts"] <= event["ts"] < horizon
                and (event.get("args") or {}).get("cue_revision") == revision
            ]
            draws_for_cue = [
                event.get("args") or {} for event in candidates if event["name"] == "subtitle_draw"
            ]
            owed = max(
                (
                    value["owed_color"]
                    for value in draws_for_cue
                    if isinstance(value.get("owed_color"), int)
                ),
                default=None,
            )
            row["owed_color"] = owed
            row["status"] = (
                "not-applicable"
                if owed == 0
                else "no-matching-ack"
                if owed
                else "unknown-eligibility"
            )
            acknowledgments = [
                event
                for event in candidates
                if _is_color_ack(event) and _completion_us(event) < horizon
            ]
            ack = min(acknowledgments, key=_completion_us, default=None)
            if ack is not None and owed:
                row.update(
                    status="acknowledged",
                    wait_ms=(_completion_us(ack) - arrival["ts"]) / 1000,
                )
        results.append(row)
    return results


def occurrence_outcomes(trace: dict) -> list[dict]:
    """Retain unfinished appearances and orphaned terminal evidence in a truncated trace."""
    rows: dict[tuple[object, object], dict] = {}
    for event in sorted(trace.get("traceEvents", ()), key=lambda item: item.get("ts", 0)):
        if event.get("ph") != "X" or event.get("name") not in {
            "subtitle_color_arrival",
            "subtitle_color_outcome",
            "subtitle_color_progress",
        }:
            continue
        args = event.get("args") or {}
        key = (args.get("color_session"), args.get("occurrence"))
        if None in key:
            continue
        row = rows.setdefault(
            key,
            {
                "color_session": key[0],
                "occurrence": key[1],
                "endpoint": "mpv-acknowledgment",
                "wait_ms": None,
                "status": "unknown-correlation",
                "arrival_recorded": False,
            },
        )
        if event["name"] == "subtitle_color_arrival":
            row.update(kind=args.get("kind"), status="pending", arrival_recorded=True)
        else:
            row.update(first_ms=None, complete_ms=None, requested=None)
            row.update(args)
            row["status"] = args.get("color_status", args.get("status"))
            row["finalized"] = event["name"] == "subtitle_color_outcome"
            if not row["finalized"]:
                row["status"] = "complete-provisional" if row["status"] == "complete" else "pending"
            row["wait_ms"] = args.get("complete_ms") if row["arrival_recorded"] else None
            if not row["arrival_recorded"]:
                row["status"] = "unknown-correlation"
    return list(rows.values())


def _completion_us(event: dict) -> float:
    return event["ts"] + event.get("dur", 0)


def _is_color_ack(event: dict) -> bool:
    args = event.get("args") or {}
    return (
        event.get("name") == "surface_write"
        and args.get("slot") == _COLOR_SLOT
        and bool(args.get("events"))
        and args.get("outcome") == "succeeded"
    )


#: The overlay slot the color payload is written to — the one write on the draw path whose cost is
#: mpv's rather than ours.
_COLOR_SLOT = "subtitle-native-focus"


@dataclass(frozen=True)
class Navigation:
    """One seek, and what became of the color it was owed."""

    wait: float | None
    cue: str
    owed: bool


def seek_waits(trace: dict) -> list[Navigation]:
    """Per navigation, how long until color was on screen — across cue identity, not within it.

    A seek draws a navigation *hint* from the cue index, and mpv's own `sub-text` follows tens of
    milliseconds later under a different digest. Ending the measurement at that change reports the
    hint's 0 ms and calls the rest somebody else's appearance, so the wait a viewer actually sits
    through — the whole point of this file — falls in the gap between two green numbers. It read
    `p50 0.0 ms` against a measured 48.6.

    Bounded by the next navigation, so a seek whose color never arrives stays `None` instead of
    borrowing the next one's.

    A seek that lands on a cue owing no color is `owed=False`, not a miss. Counting those as
    "never colored" put four punctuation-only and blank cues in the defect tail of one session —
    every content cue there had colored.
    """
    events = sorted(
        (
            event
            for event in trace.get("traceEvents", ())
            if event.get("ph") == "X" and event.get("name") in {"sub_nav_identity", "subtitle_draw"}
        ),
        key=lambda event: event["ts"],
    )
    result: list[Navigation] = []
    started: float | None = None
    for event in events:
        args = event.get("args") or {}
        if event["name"] == "sub_nav_identity":
            started = event["ts"] / 1000.0
            result.append(Navigation(None, "", owed=False))
            continue
        if started is None or args.get("path") != "native" or result[-1].wait is not None:
            continue
        cue = str(args.get("cue", ""))
        if args.get("owed_color"):
            result[-1] = Navigation(None, cue, owed=True)
        if args.get("measured_boxes") and args.get("tokens"):
            result[-1] = Navigation(event["ts"] / 1000.0 - started, cue, owed=True)
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
    parser.add_argument(
        "--presentation-manifest",
        type=Path,
        help="independent appearance census for GPU composition qualification",
    )
    args = parser.parse_args(argv)

    if args.presentation_manifest is not None:
        from saitenka.app.frame_presentation import qualify_report

        document = json.loads(args.presentation_manifest.read_text(encoding="utf-8"))
        result = qualify_report(args.report, document)
        print(json.dumps(result, indent=2))
        return 0 if result["qualified"] else 1

    evidence = trace_evidence(args.report)
    for history in metadata_timed_history(args.report):
        if history.get("status") == "partial":
            print(f"timed publication history (not display): {json.dumps(history)}")
    metrics = metadata_color_metrics(args.report)
    if metrics.get("status") == "collected":
        print(
            f"cumulative color metrics (snapshot, not per-occurrence trace): {json.dumps(metrics)}"
        )
    trace = {"traceEvents": evidence.pop("events")}
    print(f"input evidence: {json.dumps(evidence)}")
    if evidence["status"] == "invalid":
        return 1
    if evidence["status"] == "missing" and metrics.get("status") == "collected":
        return 0
    occurrences = occurrence_outcomes(trace)
    if occurrences:
        print(f"{len(occurrences)} color occurrences (acknowledgment, not physical display)")
        for kind in sorted({str(row.get("kind", "unknown")) for row in occurrences}):
            rows = [row for row in occurrences if str(row.get("kind", "unknown")) == kind]
            print(f"  {kind}: {dict(collections.Counter(row['status'] for row in rows))}")
            print(
                f"    late={sum(bool(row.get('late')) for row in rows)}"
                f" coverage withdrawals={sum(row.get('withdrawals', 0) for row in rows)}"
                f" eligibility withdrawn={sum(row.get('eligibility_withdrawn', 0) for row in rows)}"
            )
            for endpoint in ("first_ms", "complete_ms"):
                samples = sorted(
                    row[endpoint]
                    for row in rows
                    if row.get(endpoint) is not None and row["arrival_recorded"]
                )
                if samples:
                    print(
                        f"    {endpoint}: n={len(samples)}/{len(rows)}"
                        f" p50={statistics.median(samples):.1f}"
                        f" p95={samples[min(int(len(samples) * 0.95), len(samples) - 1)]:.1f}"
                        f" max={samples[-1]:.1f}"
                    )
        return 0
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
    arrival_records = color_arrivals(trace)
    print(
        f"  arrival outcomes (overprint acknowledgment): {dict(collections.Counter(row['status'] for row in arrival_records))}"
    )
    on_screen = sorted(row["wait_ms"] for row in arrival_records if row["wait_ms"] is not None)
    if on_screen:
        # The viewer's number: cue arrives -> mpv acknowledges the colored overlay. Printed first
        # because the reading below it measures from our own first draw to our own draw call, which
        # is both ends early and reported 0.0 ms for cues that took tens of milliseconds.
        print(
            f"  arrival->mpv acknowledgment (not display): n={len(on_screen)}"
            f"   p50 {statistics.median(on_screen):6.1f} ms"
            f"   p95 {on_screen[min(int(len(on_screen) * 0.95), len(on_screen) - 1)]:6.1f} ms"
            f"   max {on_screen[-1]:6.1f} ms"
        )
    if measured:
        print(
            f"  (draw to draw):  p50 {statistics.median(measured):7.1f} ms"
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
        landed = [nav.wait for nav in navigated if nav.wait is not None]
        blank = sum(nav.wait is None and nav.owed for nav in navigated)
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
