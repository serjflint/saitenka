from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

PROTOCOL = "saitenka.sidebar/v0"
CAPABILITY = "sidebar-v0"
ANSWER_KEYS = frozenset({"expect"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "given", "cases", "expect"}
        or scenario.get("mode") != "interaction"
    ):
        raise ValueError("invalid sidebar-v0 scenario")
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
        or request.get("mode") != "interaction"
        or set(request) != {"type", "protocol", "scenario_id", "mode", "given", "cases"}
    ):
        raise ValueError("invalid sidebar-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    return [_evaluate_case(case, request["given"]) for case in request["cases"]]


def _evaluate_case(case: dict, given: dict) -> object:
    tests = Path(__file__).resolve().parents[1]
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    from PIL import Image
    from saitenka_subtitles import Cue, CueIndex
    from session_builder import build_session
    from util import FakeIPC

    from saitenka.app.features.sidebar import sidebar
    from saitenka.app.subtitles import SidebarHitBox, SidebarRender
    from saitenka.runtime import events

    ipc = FakeIPC()
    ipc.props.update(case.get("props", {}))
    reader = build_session(ipc)
    reader.graph.screen.osd = tuple(given["viewport"])
    cues = [
        Cue(
            float(index * case.get("step", 1)),
            float(index * case.get("step", 1)) + 0.8,
            f"cue {index}",
        )
        for index in range(case.get("cue_count", 20))
    ]
    active = case.get("active", 0)
    if case["duplicate_text"]:
        cues[case["duplicate_text"][0]] = dataclasses.replace(
            cues[case["duplicate_text"][0]], text="same line"
        )
        cues[case["duplicate_text"][1]] = dataclasses.replace(
            cues[case["duplicate_text"][1]], text="same line"
        )
        text = "same line"
    else:
        text = f"cue {active}"
    reader.graph.track_commands.navigation.current.sub_index = CueIndex(cues)
    reader.graph.playback.install_seed({"sub-text": text})
    rendered: list[list[int]] = []
    original = sidebar.render_sidebar

    def render(rows, **_kwargs):
        rendered.append([row.value for row in rows])
        return SidebarRender(Image.new("RGBA", (1, 1)), (), 1)

    sidebar.render_sidebar = render
    try:
        op = case["op"]
        if op == "toggle":
            sidebar.show(reader.graph.sidebar.view())
            return {
                "open": reader.graph.sidebar.state.open,
                "rows": rendered[-1],
                "paused": any(command[:2] == ("set_property", "pause") for command in ipc.commands),
            }
        if op == "manual-scroll":
            sidebar.show(reader.graph.sidebar.view())
        else:
            reader.graph.sidebar.store.dispatch(
                events.SidebarShown(
                    reader.graph.sidebar.view().active, reader.graph.sidebar.view().capacity
                )
            )
        reader.graph.sidebar.panel.rect = tuple(given["panel_rect"])
        if op == "click-seek":
            reader.graph.sidebar.panel.hits = (
                SidebarHitBox("seek", case["target"], 0, 0, 200, 40),
            )
            captured = reader.graph.sidebar.on_click(
                reader.graph.interaction.click_target(), 110, 110
            )
            return {
                "captured": captured,
                "time_pos": [
                    command[2]
                    for command in ipc.commands
                    if command[:2] == ("set_property", "time-pos")
                ],
                "paused": any(command[:2] == ("set_property", "pause") for command in ipc.commands),
            }
        if op == "manual-scroll":
            ipc.props["mouse-pos"] = {"x": 110, "y": 110}
            accepted = reader.graph.sidebar.scroll(
                reader.graph.interaction.wheel_step(), case["steps"]
            )
            held = reader.graph.sidebar.state.scroll
            reader.graph.playback.install_seed({"sub-text": f"cue {case['next_active']}"})
            sidebar.follow(reader.graph.sidebar.view())
            before = len(rendered)
            before_expiry = reader.graph.sidebar.state.scroll
            fired = ipc.fire_runtime_timer("lifecycle:sidebar-manual-hold")
            return {
                "accepted": accepted,
                "held": held,
                "before_expiry": before_expiry,
                "fired": fired,
                "after": reader.graph.sidebar.state.scroll,
                "redraws": len(rendered) - before,
            }
        raise ValueError(f"unknown sidebar operation: {op!r}")
    finally:
        sidebar.render_sidebar = original
        reader.close()
