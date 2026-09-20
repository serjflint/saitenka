"""The installed-run verdict must retain demand and bind runtime evidence."""

import json

import pytest
from presentation.run import (
    input_changes_complete,
    navigation_appearances,
    producer_identity,
    scan_coverage,
)

from saitenka.app.frame_presentation import Frame


@pytest.mark.parametrize("missing", [None, "exit", "tail", "script", "transition"])
def test_input_change_control_requires_clean_completion_of_all_transitions(missing):
    records = [
        {"event": "ack", "captured_ns": 1},
        *[
            {"event": "invalidate", "captured_ns": 2, "reason": reason}
            for reason in ("sub-delay", "suspend", "osd-dimensions", "source-replaced")
        ],
    ]
    if missing == "transition":
        records.pop()
    observed = [Frame(1, 10, 10000 if missing == "tail" else 19000, False, (), 0)]
    assert input_changes_complete(
        {"returncode": 1 if missing == "exit" else 0},
        records,
        observed,
        "" if missing == "script" else "saitenka-presentation-inputs-complete",
    ) is (missing is None)


def test_navigation_early_stop_keeps_unseen_final_cues_in_census():
    population = [
        {
            "start_ms": start,
            "video_start_ms": start,
            "video_end_ms": start + 2000,
            "wall_end_ns": 10000,
        }
        for start in (6000, 8000, 12000, 16000)
    ]
    observed = [
        Frame(i, i * 1000, pts, True, (), 0)
        for i, pts in enumerate((0, 8010, 8010, 6010, 10000), 1)
    ]
    trace = {
        "traceEvents": [
            {"name": "sub_seek", "ts": i + 0.5, "args": {"target_start": target}}
            for i, target in enumerate((8, 8, 6), 1)
        ]
    }

    appearances = navigation_appearances(population, observed, trace)

    assert [a["start_ms"] for a in appearances] == [6000, 8000, 8000, 6000, 8000, 12000, 16000]


@pytest.mark.parametrize("missing", [None, "boxes", "identity", "visit"])
def test_scanning_requires_boxes_for_the_same_authored_visit(missing):
    appearance = {"text_hash": "abc", "start_ms": 16000, "wall_start_ns": 1000, "wall_end_ns": 3000}
    trace = {
        "traceEvents": [
            {
                "name": "subtitle_color_target",
                "args": {
                    "occurrence": 3,
                    "text_hash": "abc",
                    "cue_start_ms": 6000 if missing == "identity" else 16000,
                },
            },
            {
                "name": "subtitle_draw",
                "ts": 4 if missing == "visit" else 2,
                "args": {
                    "occurrence": 3,
                    "path": "native",
                    "tokens": 2,
                    "scan_tokens": 0 if missing == "boxes" else 2,
                },
            },
        ]
    }
    assert scan_coverage(trace, [appearance]) is (missing is None)


@pytest.mark.parametrize("broken", [None, "binary", "linkage", "link"])
def test_producer_identity_binds_statically_linked_libass_to_player(tmp_path, broken):
    binary = tmp_path / "mpv/build/mpv"
    binary.parent.mkdir(parents=True)
    build = {
        "libass_library": "/prefix/lib/libass.a",
        "libass_sha256": "a" * 64,
        "binary_sha256": "b" * 64,
        "libass_linkage": "static",
        "link_command": "cc main.o /prefix/lib/libass.a -o mpv",
    }
    if broken == "binary":
        build["binary_sha256"] = "c" * 64
    elif broken == "linkage":
        build["libass_linkage"] = "shared"
    elif broken == "link":
        build["link_command"] = "cc -lass -o mpv"
    (tmp_path / "receipt.json").write_text(json.dumps(build), encoding="utf-8")
    receipt = {"binary": str(binary), "binary_sha256": "b" * 64}
    if broken:
        with pytest.raises(ValueError, match="producer identity"):
            producer_identity(receipt)
    else:
        assert producer_identity(receipt)["sha256"] == "a" * 64
