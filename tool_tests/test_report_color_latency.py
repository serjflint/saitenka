"""The wait-to-color readout, including the cases its durations cannot express."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "report_color_latency",
    Path(__file__).resolve().parents[1] / "tools" / "report_color_latency.py",
)
assert _spec and _spec.loader
latency = importlib.util.module_from_spec(_spec)
sys.modules["report_color_latency"] = latency
_spec.loader.exec_module(latency)


def draw(ts_ms: float, cue: str, boxes: int, path: str = "native", *, tokens: int = 6) -> dict:
    return {
        "ph": "X",
        "name": "subtitle_draw",
        "ts": ts_ms * 1000.0,
        "args": {"cue": cue, "measured_boxes": boxes, "path": path, "tokens": tokens},
    }


def decision(ts_ms: float, outcome: str, eligible: int, reason: str = "ready") -> dict:
    return {
        "ph": "X",
        "name": "subtitle_geometry_decision",
        "ts": ts_ms * 1000.0,
        "args": {"outcome": outcome, "eligible_tokens": eligible, "reason": reason},
    }


def read(events: list[dict]) -> list[latency.Appearance]:
    trace = {"traceEvents": events}
    return latency.appearances(latency.draws(trace), latency.decisions(trace))


def test_the_wait_is_the_gap_to_the_first_draw_that_carried_boxes() -> None:
    """Not the last, and not the geometry's completion — the first draw color could be seen in."""
    shown = read([draw(100, "aa", 0), draw(140, "aa", 0), draw(180, "aa", 7), draw(220, "aa", 7)])

    assert [item.wait for item in shown] == [80.0]


def test_a_cue_that_recurs_later_is_two_appearances_not_one() -> None:
    """The handle digests *content*, so a repeated line returns under the one it had earlier and
    grouping by handle welds separate showings together. mpv shows one cue at a time, so an
    intervening draw of another ends this one."""
    shown = read(
        [
            draw(0, "repeat", 0),
            draw(40, "repeat", 0),
            draw(900, "other", 5),
            draw(17_000, "repeat", 0),
        ]
    )

    assert [(item.cue, item.draws, item.held) for item in shown] == [
        ("repeat", 2, 900.0),
        ("other", 1, 16_100.0),
        ("repeat", 1, float("inf")),
    ]


def test_an_appearance_owed_no_color_leaves_the_denominator() -> None:
    """A cue whose tokens were all skipped is drawn with zero boxes and is correct. It leaves the
    denominator rather than moving to the numerator's other side."""
    shown = read([draw(0, "empty", 0), decision(10, "ready", 0), draw(20, "empty", 0)])

    assert [(item.eligible, item.owed_color) for item in shown] == [(0, False)]


def test_a_pending_decision_is_the_question_not_the_answer() -> None:
    """`pending` carries `eligible_tokens=0` because the observation has not arrived. Reading that
    as "nothing to paint" files every slow cue as one that wanted no color."""
    shown = read([draw(0, "slow", 0), decision(10, "pending", 0), draw(20, "slow", 0)])

    assert [(item.eligible, item.owed_color) for item in shown] == [(None, True)]


def test_a_window_with_no_decision_is_unknown_rather_than_zero() -> None:
    """Absent is not zero: defaulting it excuses every appearance whose decision the trace lacks."""
    shown = read([draw(0, "quiet", 0)])

    assert shown[0].eligible is None
    assert shown[0].owed_color


def test_a_decision_belonging_to_the_next_appearance_is_not_borrowed() -> None:
    """The windows are half-open: the decision settling the *next* cue cannot excuse this one."""
    shown = read([draw(0, "aa", 0), draw(500, "bb", 0), decision(510, "ready", 0)])

    assert [item.eligible for item in shown] == [None, 0]


def test_a_never_colored_appearance_carries_how_long_it_held_the_screen() -> None:
    """Screen time separates a real miss from a flash gone before anyone could read it."""
    shown = read([draw(0, "flash", 0), draw(70, "held", 0), draw(900, "next", 1)])

    assert [(item.cue, item.held) for item in shown[:2]] == [("flash", 70.0), ("held", 830.0)]
    assert [item.held < latency.GLIMPSE_MS for item in shown[:2]] == [True, False]


def test_the_last_appearance_of_a_session_is_not_given_an_invented_screen_time() -> None:
    """Nothing bounds it. Treating that as zero files every session's final cue as a flash — the
    one place this heuristic could hide a real miss."""
    shown = read([draw(0, "only", 0)])

    assert shown[0].held == float("inf")


def test_legacy_draws_are_not_counted_as_uncolored_native_ones() -> None:
    """The legacy renderer colors the cue itself and publishes no boxes."""
    shown = read([draw(0, "aa", 0, path="legacy"), draw(10, "aa", 0, "legacy")])

    assert shown == []


def test_boxes_with_no_tokens_to_put_them_on_are_not_a_paint() -> None:
    """Geometry published against a cue that cannot use it. Scoring it as colored reports a paint
    that never happened."""
    shown = read([draw(0, "orphan", 3, tokens=0), draw(10, "orphan", 3, tokens=0)])

    assert shown[0].wait is None
    assert shown[0].orphan_boxes == 2


def test_boxes_with_tokens_are_still_a_paint() -> None:
    """The guard must not swallow a real colouring."""
    shown = read([draw(0, "real", 0, tokens=6), draw(10, "real", 3, tokens=6)])

    assert (shown[0].wait, shown[0].orphan_boxes) == (10.0, 0)


def test_a_draw_reports_what_caused_it_not_what_preceded_it() -> None:
    """A draw parented to `subtitle_geometry_apply` is a redraw the geometry side triggered — the
    moment the cue side may have moved on. Adjacency cannot distinguish that."""
    trace = {
        "traceEvents": [
            {"ph": "X", "name": "cue_redraw", "ts": 0, "args": {"span_id": "a"}},
            {
                "ph": "X",
                "name": "subtitle_draw",
                "ts": 1,
                "args": {"span_id": "b", "parent_id": "a"},
            },
            {"ph": "X", "name": "subtitle_geometry_apply", "ts": 2, "args": {"span_id": "c"}},
            {
                "ph": "X",
                "name": "subtitle_draw",
                "ts": 3,
                "args": {"span_id": "d", "parent_id": "c"},
            },
        ]
    }

    assert latency.caused_by(trace, "subtitle_draw") == {
        "b": "cue_redraw",
        "d": "subtitle_geometry_apply",
    }


def test_a_draw_with_no_parent_is_reported_as_rooted_not_guessed() -> None:
    """Absent causation is not the nearest earlier span."""
    trace = {
        "traceEvents": [
            {"ph": "X", "name": "cue_redraw", "ts": 0, "args": {"span_id": "a"}},
            {"ph": "X", "name": "subtitle_draw", "ts": 1, "args": {"span_id": "b"}},
        ]
    }

    assert latency.caused_by(trace, "subtitle_draw") == {"b": "root"}


def test_a_decision_for_a_different_cue_is_not_borrowed_by_handle() -> None:
    """The window alone let an adjacent cue's decision answer for this one."""
    events = [
        draw(0, "mine", 0, tokens=6),
        {
            "ph": "X",
            "name": "subtitle_geometry_decision",
            "ts": 5_000.0,
            "args": {"outcome": "ready", "eligible_tokens": 0, "reason": "ready", "cue": "theirs"},
        },
    ]

    assert read(events)[0].eligible is None, "a decision naming another cue must not answer here"


def test_the_wait_a_viewer_lives_spans_two_cue_handles() -> None:
    """A `sub-seek` redraws with text whose rows have not arrived, so the provisional draw and the
    settled one hash to different handles. Each half looks fast and the wait belongs to neither."""
    events = [
        draw(0, "provisional", 0),
        decision(1, "pending", 0, reason="subtitle-observation-pending"),
        decision(70, "ready", 6),
        draw(71, "settled", 6),
    ]
    shown = read(events)
    assert [item.wait for item in shown] == [None, 0.0]  # neither half sees the interval

    assert latency.settling(latency.decisions({"traceEvents": events})) == [69.0]


def test_a_cache_miss_pending_is_not_an_unsettled_observation() -> None:
    """Only `subtitle-observation-pending` means the text is up without its rows. A cache miss
    means the rows are known and the render queued — already measured per appearance."""
    events = [
        decision(0, "pending", 6, reason="subtitle-geometry-cache-miss"),
        decision(9, "ready", 6),
    ]

    assert latency.settling(latency.decisions({"traceEvents": events})) == []


def test_an_observation_that_never_settled_contributes_no_interval() -> None:
    """Better one fewer sample than a bound taken from a `ready` that never came."""
    events = [decision(0, "pending", 0, reason="subtitle-observation-pending")]

    assert latency.settling(latency.decisions({"traceEvents": events})) == []


def test_a_bundle_without_the_cue_handle_is_refused_rather_than_summarised(tmp_path: Path) -> None:
    """Without the handle every draw looks like one cue. A plausible wrong number is worse than
    none."""
    bundle = tmp_path / "old.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "telemetry/trace.json",
            json.dumps(
                {"traceEvents": [{"ph": "X", "name": "subtitle_draw", "ts": 0, "args": {}}]}
            ),
        )

    assert latency.main([str(bundle)]) == 1


def test_a_bundle_with_no_draws_at_all_is_refused(tmp_path: Path) -> None:
    bundle = tmp_path / "empty.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("telemetry/trace.json", json.dumps({"traceEvents": []}))

    assert latency.main([str(bundle)]) == 1


@pytest.mark.parametrize("boxes", [1, 12])
def test_any_positive_box_count_counts_as_colored(boxes: int) -> None:
    shown = read([draw(0, "aa", 0), draw(25, "aa", boxes)])

    assert [item.wait for item in shown] == [25.0]
