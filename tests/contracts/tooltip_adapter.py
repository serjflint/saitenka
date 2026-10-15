from __future__ import annotations

import sys
from itertools import starmap
from pathlib import Path

PROTOCOL = "saitenka.tooltip/v0"
CAPABILITY = "tooltip-v0"
ANSWER_KEYS = frozenset({"expect"})
MODES = frozenset({"geometry", "hit-testing", "interaction"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario)
        not in (
            {"scenario_id", "mode", "cases", "expect"},
            {"scenario_id", "mode", "given", "cases", "expect"},
        )
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
        or set(request)
        not in (
            {"type", "protocol", "scenario_id", "mode", "cases"},
            {"type", "protocol", "scenario_id", "mode", "given", "cases"},
        )
    ):
        raise ValueError("invalid tooltip-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    if request["mode"] == "hit-testing":
        return [_hit_test(case) for case in request["cases"]]
    if request["mode"] == "interaction":
        return [_interaction(case, request["given"]) for case in request["cases"]]
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


def _interaction(case: dict, given: dict) -> object:
    tests = Path(__file__).resolve().parents[1]
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    from driver import Driver
    from saitenka_tokenize.japanese import Token
    from session_builder import build_session
    from util import FakeIPC

    from saitenka.app.config import ReaderOptions
    from saitenka.app.features.tooltip import tooltip, tooltip_panel
    from saitenka.app.session.factory import SessionServices
    from saitenka.app.subtitle_render import NullRenderer
    from saitenka.app.subtitles import WordBox
    from saitenka.panel import Definition, Entry

    class Dictionary:
        def entry_for(self, _token, inflected=None, *, extra_terms=()):  # noqa: ARG002
            return Entry(
                headword=given["dictionary"]["headword"],
                reading=given["dictionary"]["reading"],
                defs=[
                    Definition(item["source"], item["content"])
                    for item in given["dictionary"]["definitions"]
                ],
            )

        def search(self, _pattern):
            return self.entry_for(None)

        def rareness_rank(self, _token):
            return None

    session = build_session(
        FakeIPC(),
        services=SessionServices(dictionaries=Dictionary()),
        options=ReaderOptions().with_overrides(scan_delay=0.0),
    )
    graph = session.graph
    graph.screen.osd = tuple(given["viewport"])
    graph.subtitle_presentation.cue.replace_tokenized(
        tokens=[Token(**token) for token in given["cue"]["tokens"]]
    )
    graph.subtitle_presentation.cue.replace_geometry(
        origin=tuple(given["cue"]["origin"]),
        boxes=list(starmap(WordBox, given["cue"]["boxes"])),
    )
    graph.tooltip.visual.crisp = given["crisp"]
    graph.subtitle_presentation.renderer = NullRenderer()
    driver = Driver(session, instant=False)
    copied: list[str] = []
    original_copy = tooltip.copy_clipboard
    tooltip.copy_clipboard = copied.append
    try:
        driver.move_to_word(0)
        op = case["op"]
        if op == "hover-copy":
            rect = graph.tooltip.surface_state().view.rect
            assert rect is not None
            driver.move(rect[0] + rect[2] / 2, rect[1] + 5).right_click()
            return {
                "shown": graph.tooltip.hover_view().tip.shown,
                "selected": graph.tooltip.observation().selected,
                "copied": copied,
                "pulse": str(graph.tooltip.observation().pulse.overlay),
            }
        if op == "link-navigation":
            view = graph.tooltip.surface_state().view
            panel, scale, scroll = tooltip_panel.hit_target(
                graph.tooltip.surface_state().nest,
                view.state,
                view.scroll,
                graph.tooltip.scale().raster,
                nested=False,
            )
            assert panel is not None and view.xy is not None
            panel.windowed.viewport(0, 1_000_000)
            link = next(
                box for box in panel.windowed.link_boxes() if not box.query.startswith("kanji:")
            )
            x = view.xy[0] + (link.x + link.w / 2) * scale
            y = view.xy[1] + (link.y + link.h / 2 - scroll) * scale
            driver.move(x, y).click()
            return {
                "navigation_depth": graph.tooltip.observation().navigation_depth,
                "shown": graph.tooltip.hover_view().tip.shown,
            }
        if op == "pause-ownership":
            paused = [
                command[2]
                for command in graph.ipc.commands
                if command[:2] == ("set_property", "pause")
            ]
            owned_before = graph.tooltip.observation().pause.held
            graph.tooltip.retire_hover()
            return {
                "pause_commands": paused,
                "owned_before_leave": owned_before,
                "owned_after_leave": graph.tooltip.observation().pause.held,
            }
        raise ValueError(f"unknown tooltip interaction operation: {op!r}")
    finally:
        tooltip.copy_clipboard = original_copy
        session.close()


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
