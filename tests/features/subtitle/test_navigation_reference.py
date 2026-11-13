"""Cue navigation reference data, including repeated and filtered text."""

import json
from pathlib import Path

import pytest
from saitenka_subtitles import Cue, CueIndex

from saitenka.app import subnav_policy

DATA = json.loads(
    (Path(__file__).parents[2] / "fixtures/subtitle_reference.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("record", DATA["on-screen"], ids=lambda r: r["id"])
def test_visible_cue_reference(record):
    v = record["input"]
    assert (
        subnav_policy.cue_is_on_screen(
            Cue(**v["cue"]), text=v["text"], sub_start=v["sub_start"], time_pos=v["time_pos"]
        )
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["navigation"], ids=lambda r: r["id"])
def test_navigation_target_reference(record):
    v = record["input"]
    target = subnav_policy.resolve_target(
        CueIndex([Cue(**c) for c in v["cues"]]),
        delta=v["delta"],
        text=v["text"],
        sub_start=v["sub_start"],
        time_pos=v["time_pos"],
        nav_idx=v["nav_idx"],
    )
    actual = {"start": target.cue.start, "text": target.cue.text} if target is not None else None
    assert actual == record["expected"]


@pytest.mark.parametrize("record", DATA["filters-can-drop"], ids=lambda r: r["id"])
def test_filter_visibility_reference(record):
    assert subnav_policy.filters_can_drop_a_cue(record["input"]["settings"]) == record["expected"]
