"""Tooltip geometry inputs and expected coordinates, independent of panel contents."""

import json
from itertools import starmap
from pathlib import Path

import pytest

from saitenka.app.features.tooltip.nested_popup import nested_view_h
from saitenka.app.features.tooltip.prefetch import cap_for, tip_scale
from saitenka.app.features.tooltip.tooltip_panel import place_panel
from saitenka.app.subtitles import WordBox, token_at

DATA = json.loads(
    (Path(__file__).parents[2] / "fixtures/tooltip_reference.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("record", DATA["scale"], ids=lambda r: r["id"])
def test_tooltip_scale_reference(record):
    scale = tip_scale(**record["input"])
    assert {"display": scale.display, "raster": scale.raster, "cap": scale.cap} == record[
        "expected"
    ]


@pytest.mark.parametrize("record", DATA["cap"], ids=lambda r: r["id"])
def test_tooltip_height_cap_reference(record):
    assert cap_for(record["input"]["fraction"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["nested-height"], ids=lambda r: r["id"])
def test_nested_tooltip_height_reference(record):
    v = record["input"]
    assert (
        nested_view_h(v["full_h"], v["word_y"], osd_h=v["osd_h"], max_frac=v["max_frac"])
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["place"], ids=lambda r: r["id"])
def test_tooltip_placement_reference(record):
    v = record["input"]
    assert (
        list(
            place_panel(
                v["width"],
                v["word_x"],
                v["word_y"],
                v["word_h"],
                v["height"],
                scale=v["scale"],
                osd=tuple(v["osd"]),
            )
        )
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["hit-testing"], ids=lambda r: r["id"])
def test_subtitle_token_hit_reference(record):
    v = record["input"]
    assert (
        token_at(
            list(starmap(WordBox, v["boxes"])),
            tuple(v["point"]),
            tuple(v["origin"]),
            is_skippable=frozenset(v["skippable"]).__contains__,
        )
        == record["expected"]
    )
