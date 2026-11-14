"""Recorded mpv render settings and native geometry qualification outcomes."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest
from dirty_equals import IsPartialDict

from saitenka.app.native_subtitles import _scaled_renderer_state, render_inputs_of

DATA = json.loads(
    (Path(__file__).parent / "fixtures/subtitle_reference.json").read_text(encoding="utf-8")
)


def _inputs(v):
    def numbers(values):
        return {
            k: float(value[1:]) if isinstance(value, str) and value in {"$nan", "$inf"} else value
            for k, value in values.items()
        }

    return render_inputs_of(
        {
            "w": 1920,
            "h": 1080,
            "mt": 0,
            "mb": 0,
            "ml": 0,
            "mr": 0,
            "par": 1.0,
            **numbers(v.get("osd", {})),
        },
        {"w": 1920, "h": 1080, "par": 1.0, **numbers(v.get("video", {}))},
        DATA["render_defaults"] | numbers(v.get("settings", {})),
        frame_size=tuple(v.get("frame_size", [1920, 1080])),
        authored=v.get("authored", True),
    )


@pytest.mark.parametrize(
    "record",
    [r for r in DATA["render-inputs"] if "error" not in r["expected"]],
    ids=lambda r: r["id"],
)
def test_native_render_input_values(record):
    result = _inputs(record["input"])
    actual = json.loads(json.dumps(asdict(result)))
    if "font_option" in record["input"]:
        actual["font_option_accepted"] = any(
            k == record["input"]["font_option"] for k, _ in result.font_options
        )
    assert actual == IsPartialDict(**record["expected"])


@pytest.mark.parametrize(
    "record", [r for r in DATA["render-inputs"] if "error" in r["expected"]], ids=lambda r: r["id"]
)
def test_native_render_input_refusals(record):
    with pytest.raises(ValueError, match=re.escape(record["expected"]["error"])):
        _inputs(record["input"])


@pytest.mark.parametrize("record", DATA["renderer-state"], ids=lambda r: r["id"])
def test_native_renderer_scale_reference(record):
    state = _scaled_renderer_state(_inputs(record["input"]).scale)
    assert {
        "font_scale": state.font_scale,
        "line_position": state.line_position,
        "line_spacing": state.line_spacing,
        "hinting": state.hinting,
        "selective_font_scale": state.selective_font_scale,
    } == record["expected"]
