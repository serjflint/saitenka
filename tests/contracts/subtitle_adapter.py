from __future__ import annotations

from typing import Any

PROTOCOL = "saitenka.subtitle/v0"
CAPABILITY = "subtitle-v0"
ANSWER_KEYS = frozenset({"expect"})
MODE_KEYS = {
    "render-inputs": frozenset({"scenario_id", "mode", "cases", "expect"}),
    "renderer-state": frozenset({"scenario_id", "mode", "cases", "expect"}),
}
BASELINE_SETTINGS = {
    "sub-ass-override": "no",
    "sub-ass-scale-with-window": False,
    "sub-scale": 1.0,
    "sub-pos": 100.0,
    "sub-use-margins": True,
    "sub-ass-force-margins": False,
    "sub-ass-video-aspect-override": None,
    "sub-ass-use-video-data": "all",
    "sub-ass-style-overrides": None,
    "sub-scale-with-window": True,
    "sub-scale-by-window": True,
    "blend-subtitles": False,
    "sub-filter-sdh": False,
    "video-crop": "",
    "video-rotate": 0,
    "sub-font-provider": "auto",
    "embeddedfonts": False,
    "sub-fonts-dir": None,
    "sub-shaper": "complex",
    "sub-ass-justify": False,
    "sub-line-spacing": 0.0,
    "sub-hinting": "none",
    "sub-scale-signs": False,
}


def scenario_request(scenario: dict) -> dict:
    mode = scenario.get("mode")
    allowed = MODE_KEYS.get(mode)
    if allowed is None or set(scenario) != allowed:
        raise ValueError(f"invalid subtitle-v0 scenario for {mode!r}")
    return {
        "type": "run",
        "protocol": PROTOCOL,
        **{key: value for key, value in scenario.items() if key not in ANSWER_KEYS},
    }


def validate_request(request: dict) -> None:
    if ANSWER_KEYS & request.keys():
        raise ValueError("subtitle-v0 requests cannot contain answer keys")
    mode = request.get("mode")
    allowed = MODE_KEYS.get(mode)
    expected = ((allowed or frozenset()) - ANSWER_KEYS) | {"type", "protocol"}
    if (
        allowed is None
        or set(request) != expected
        or request.get("type") != "run"
        or request.get("protocol") != PROTOCOL
    ):
        raise ValueError("invalid subtitle-v0 request envelope")


def _decode(value: object) -> object:
    if value == "$nan":
        return float("nan")
    if value == "$inf":
        return float("inf")
    return value


def _normalize(value: object) -> Any:
    if isinstance(value, tuple | list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


def _render_inputs(case: dict) -> dict:
    from saitenka.app.native_subtitles import render_inputs_of

    osd = {"w": 1920, "h": 1080, "mt": 0, "mb": 0, "ml": 0, "mr": 0, "par": 1.0}
    video = {"w": 1920, "h": 1080, "par": 1.0}
    osd.update({key: _decode(value) for key, value in case.get("osd", {}).items()})
    video.update({key: _decode(value) for key, value in case.get("video", {}).items()})
    settings = dict(BASELINE_SETTINGS)
    settings.update({key: _decode(value) for key, value in case.get("settings", {}).items()})
    try:
        result = render_inputs_of(
            osd,
            video,
            settings,
            frame_size=tuple(case.get("frame_size", [1920, 1080])),
            authored=case.get("authored", True),
        )
    except ValueError as exc:
        return {"error": str(exc).partition("=")[0]}
    observed = {key: _normalize(getattr(result, key)) for key in case["observe"]}
    if "font_option" in case:
        name = case["font_option"]
        observed["font_option_accepted"] = any(key == name for key, _value in result.font_options)
    return observed


def _renderer_state(case: dict) -> dict:
    from saitenka.app.native_subtitles import _scaled_renderer_state

    rendered = _render_inputs(case | {"observe": []})
    if "error" in rendered:
        return rendered
    from saitenka.app.native_subtitles import render_inputs_of

    settings = dict(BASELINE_SETTINGS)
    settings.update(case.get("settings", {}))
    inputs = render_inputs_of(
        {"w": 1920, "h": 1080, "mt": 0, "mb": 0, "ml": 0, "mr": 0, "par": 1.0},
        {"w": 1920, "h": 1080, "par": 1.0},
        settings,
        frame_size=(1920, 1080),
        authored=True,
    )
    state = _scaled_renderer_state(inputs.scale)
    return {
        "font_scale": state.font_scale,
        "line_position": state.line_position,
        "line_spacing": state.line_spacing,
        "hinting": state.hinting,
        "selective_font_scale": state.selective_font_scale,
    }


def evaluate(request: dict) -> object:
    validate_request(request)
    evaluator = _render_inputs if request["mode"] == "render-inputs" else _renderer_state
    return [evaluator(case) for case in request["cases"]]
