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
    """Not the last, and not the geometry's own completion — the first draw a viewer could have seen
    color in."""
    shown = read([draw(100, "aa", 0), draw(140, "aa", 0), draw(180, "aa", 7), draw(220, "aa", 7)])

    assert [item.wait for item in shown] == [80.0]


def test_a_cue_that_recurs_later_is_two_appearances_not_one() -> None:
    """The handle is a digest of the cue's *content*, so a repeated line comes back under the handle
    it had seconds ago. A field session welded a two-character interjection's 4.8 s and 21.8 s
    showings into one "cue" with eight draws, and reported a screen time spanning the gap between
    them. mpv shows one cue at a time, so an intervening draw of another cue ends this one.
    """
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
    """A cue whose tokens the tokenizer all skipped is drawn with zero boxes and is *correct*. Both
    "never colored" cues chased in a field session were this: `chars=2`, both tokens skipped,
    `eligible_tokens=0`, decision `ready`. Counting them as failures is how a healthy session read
    as broken. They leave the denominator rather than moving to the numerator's other side.
    """
    shown = read([draw(0, "empty", 0), decision(10, "ready", 0), draw(20, "empty", 0)])

    assert [(item.eligible, item.owed_color) for item in shown] == [(0, False)]


def test_a_pending_decision_is_the_question_not_the_answer() -> None:
    """`pending` carries `eligible_tokens=0` because the observation has not arrived yet. Reading
    that as "nothing to paint" would file every slow cue as one that wanted no color — the exact
    inversion of the bug being chased."""
    shown = read([draw(0, "slow", 0), decision(10, "pending", 0), draw(20, "slow", 0)])

    assert [(item.eligible, item.owed_color) for item in shown] == [(None, True)]


def test_a_window_with_no_decision_is_unknown_rather_than_zero() -> None:
    """Absent is not zero. Defaulting it to zero would silently excuse every appearance whose
    decision the trace happened not to hold."""
    shown = read([draw(0, "quiet", 0)])

    assert shown[0].eligible is None
    assert shown[0].owed_color


def test_a_decision_belonging_to_the_next_appearance_is_not_borrowed() -> None:
    """The windows are half-open, so the decision that settles the *next* cue cannot retroactively
    excuse this one."""
    shown = read([draw(0, "aa", 0), draw(500, "bb", 0), decision(510, "ready", 0)])

    assert [item.eligible for item in shown] == [None, 0]


def test_a_never_colored_appearance_carries_how_long_it_held_the_screen() -> None:
    """Screen time separates a real miss from a flash. Without it the headline overstates badly: a
    field session read 6 of 18 uncolored, and four had been on screen for 60-92 ms — gone before
    anyone could read them, let alone notice the color was absent."""
    shown = read([draw(0, "flash", 0), draw(70, "held", 0), draw(900, "next", 1)])

    assert [(item.cue, item.held) for item in shown[:2]] == [("flash", 70.0), ("held", 830.0)]
    assert [item.held < latency.GLIMPSE_MS for item in shown[:2]] == [True, False]


def test_the_last_appearance_of_a_session_is_not_given_an_invented_screen_time() -> None:
    """It has no successor to bound it. Treating that as zero would file every session's final cue
    as a flash, which is the one place the heuristic would silently hide a real miss."""
    shown = read([draw(0, "only", 0)])

    assert shown[0].held == float("inf")


def test_legacy_draws_are_not_counted_as_uncolored_native_ones() -> None:
    """The legacy renderer colors the cue itself and publishes no measured boxes, so counting its
    draws here would report every legacy cue as a native failure."""
    shown = read([draw(0, "aa", 0, path="legacy"), draw(10, "aa", 0, "legacy")])

    assert shown == []


def test_boxes_with_no_tokens_to_put_them_on_are_not_a_paint() -> None:
    """Geometry published against a cue that cannot use it. The field showed three boxes belonging
    to `1535aaa2` filed against `36e9d246`, a cue with zero tokens — and this readout scored it as
    a success, which is how a cue that painted nothing read as coloured for two sessions."""
    shown = read([draw(0, "orphan", 3, tokens=0), draw(10, "orphan", 3, tokens=0)])

    assert shown[0].wait is None
    assert shown[0].orphan_boxes == 2


def test_boxes_with_tokens_are_still_a_paint() -> None:
    """The negative half — the guard must not swallow a real colouring."""
    shown = read([draw(0, "real", 0, tokens=6), draw(10, "real", 3, tokens=6)])

    assert (shown[0].wait, shown[0].orphan_boxes) == (10.0, 0)


def test_the_wait_a_viewer_lives_spans_two_cue_handles() -> None:
    """A `sub-seek` redraws optimistically with text whose rows have not arrived, so the
    provisional draw and the settled one carry *different* text and hash to different handles.
    Per appearance each half looks fast — the provisional is a sub-100 ms flash, the settled colors
    in 0.0 ms — and the 70 ms between them belongs to neither. Derived from the decisions instead,
    which need no grouping at all.
    """
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
    """`pending` has more than one reason. Only `subtitle-observation-pending` means the text is up
    without its rows; a cache miss means the rows are known and the render is queued, which is the
    interval the per-appearance wait already measures."""
    events = [
        decision(0, "pending", 6, reason="subtitle-geometry-cache-miss"),
        decision(9, "ready", 6),
    ]

    assert latency.settling(latency.decisions({"traceEvents": events})) == []


def test_an_observation_that_never_settled_contributes_no_interval() -> None:
    """Better to report one fewer sample than to bound it with a `ready` that never came."""
    events = [decision(0, "pending", 0, reason="subtitle-observation-pending")]

    assert latency.settling(latency.decisions({"traceEvents": events})) == []


def test_a_bundle_without_the_cue_handle_is_refused_rather_than_summarised(tmp_path: Path) -> None:
    """An older bundle has the spans but not the handle, so every draw looks like one cue. Refusing
    is the point: a plausible wrong number is worse than none."""
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
