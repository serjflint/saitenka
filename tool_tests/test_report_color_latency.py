"""The wait-to-color readout, including the case its durations cannot express."""

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


def draw(ts_ms: float, cue: str, boxes: int, path: str = "native") -> dict:
    return {
        "ph": "X",
        "name": "subtitle_draw",
        "ts": ts_ms * 1000.0,
        "args": {"cue": cue, "measured_boxes": boxes, "path": path},
    }


def test_the_wait_is_the_gap_to_the_first_draw_that_carried_boxes() -> None:
    """Not the last, and not the geometry's own completion — the first draw a viewer could have seen
    color in."""
    spans = latency.draws(
        {
            "traceEvents": [
                draw(100, "aa", 0),
                draw(140, "aa", 0),
                draw(180, "aa", 7),
                draw(220, "aa", 7),
            ]
        }
    )

    measured, never, cues = latency.waits(spans)

    assert (measured, never, cues) == ([80.0], [], ["aa"])


def test_a_cue_that_was_never_colored_is_reported_apart_from_the_durations() -> None:
    """It contributes no duration, so a summary built from durations alone reports the session as
    fast by omitting exactly the failures — which is the shape of the bug being chased."""
    spans = latency.draws(
        {
            "traceEvents": [
                draw(0, "aa", 0),
                draw(40, "aa", 0),
                draw(800, "bb", 0),
                draw(810, "bb", 3),
            ]
        }
    )

    measured, never, cues = latency.waits(spans)

    assert measured == [10.0]
    assert never == [("aa", 800.0)]
    assert len(cues) == 2


def test_a_never_colored_cue_carries_how_long_it_held_the_screen() -> None:
    """Screen time is the gap to the next cue, and it is what separates a real miss from a flash.

    Without it the headline overstates badly: a field session read 6 of 18 cues uncolored, and four
    of those six had been on screen for 60-92 ms — gone before anyone could read them, let alone
    notice the color was absent. Reported as one number that is 33%; split, it is 11%.
    """
    spans = latency.draws(
        {"traceEvents": [draw(0, "flash", 0), draw(70, "held", 0), draw(900, "next", 1)]}
    )

    _measured, never, _cues = latency.waits(spans)

    assert never == [("flash", 70.0), ("held", 830.0)]
    assert [held < latency.GLIMPSE_MS for _cue, held in never] == [True, False]


def test_the_last_cue_of_a_session_is_not_given_an_invented_screen_time() -> None:
    """It has no successor to bound it. Treating that as zero would file every session's final cue
    as a flash, which is the one place the heuristic would silently hide a real miss."""
    spans = latency.draws({"traceEvents": [draw(0, "only", 0)]})

    _measured, never, _cues = latency.waits(spans)

    assert never == [("only", float("inf"))]


def test_legacy_draws_are_not_counted_as_uncolored_native_ones() -> None:
    """The legacy renderer colors the cue itself and publishes no measured boxes, so counting its
    draws here would report every legacy cue as a native failure."""
    spans = latency.draws(
        {"traceEvents": [draw(0, "aa", 0, path="legacy"), draw(10, "aa", 0, "legacy")]}
    )

    measured, never, cues = latency.waits(spans)

    assert (measured, never, cues) == ([], [], [])


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
    spans = latency.draws({"traceEvents": [draw(0, "aa", 0), draw(25, "aa", boxes)]})

    assert latency.waits(spans)[0] == [25.0]
