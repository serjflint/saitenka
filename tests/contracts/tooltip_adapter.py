from __future__ import annotations

from itertools import starmap

PROTOCOL = "saitenka.tooltip/v0"
CAPABILITY = "tooltip-v0"
ANSWER_KEYS = frozenset({"expect"})
MODES = frozenset({"geometry", "hit-testing"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "cases", "expect"}
        or scenario.get("mode") not in MODES
    ):
        raise ValueError("invalid tooltip-v0 scenario")
    return {
        "type": "run",
        "protocol": PROTOCOL,
        **{key: value for key, value in scenario.items() if key not in ANSWER_KEYS},
    }


def validate_request(request: dict) -> None:
    if (
        ANSWER_KEYS & request.keys()
        or request.get("type") != "run"
        or request.get("protocol") != PROTOCOL
        or request.get("mode") not in MODES
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid tooltip-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    if request["mode"] == "hit-testing":
        return [_hit_test(case) for case in request["cases"]]
    return [_evaluate_case(case) for case in request["cases"]]


def _hit_test(case: dict) -> int:
    from saitenka.app.subtitles import WordBox, token_at

    boxes = list(starmap(WordBox, case["boxes"]))
    skippable = frozenset(case["skippable"])
    return token_at(
        boxes,
        tuple(case["point"]),
        tuple(case["origin"]),
        is_skippable=skippable.__contains__,
    )


def _evaluate_case(case: dict) -> object:
    op = case["op"]
    if op == "scale":
        from saitenka.app.features.tooltip.prefetch import tip_scale

        scale = tip_scale(case["osd_h"], override=case["override"], max_frac=case["max_frac"])
        return {"display": scale.display, "raster": scale.raster, "cap": scale.cap}
    if op == "cap":
        from saitenka.app.features.tooltip.prefetch import cap_for

        return cap_for(case["fraction"])
    if op == "nested-height":
        from saitenka.app.features.tooltip.nested_popup import nested_view_h

        return nested_view_h(
            case["full_h"],
            case["word_y"],
            osd_h=case["osd_h"],
            max_frac=case["max_frac"],
        )
    if op == "place":
        from saitenka.app.features.tooltip.tooltip_panel import place_panel

        return list(
            place_panel(
                case["width"],
                case["word_x"],
                case["word_y"],
                case["word_h"],
                case["height"],
                scale=case["scale"],
                osd=tuple(case["osd"]),
            )
        )
    raise ValueError(f"unknown tooltip geometry operation: {op!r}")
