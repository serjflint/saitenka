from __future__ import annotations

import sys
from pathlib import Path

PROTOCOL = "saitenka.picker/v0"
CAPABILITY = "picker-v0"
ANSWER_KEYS = frozenset({"expect"})


def scenario_request(scenario: dict) -> dict:
    if (
        set(scenario) != {"scenario_id", "mode", "cases", "expect"}
        or scenario.get("mode") != "interaction"
    ):
        raise ValueError("invalid picker-v0 scenario")
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
        or set(request) != {"type", "protocol", "scenario_id", "mode", "cases"}
    ):
        raise ValueError("invalid picker-v0 request envelope")


def evaluate(request: dict) -> list[object]:
    validate_request(request)
    return [_evaluate_case(case) for case in request["cases"]]


def _evaluate_case(case: dict) -> object:
    tests = Path(__file__).resolve().parents[1]
    if str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    from session_builder import build_session
    from util import FakeIPC

    from saitenka.app import bindings, subtitle_modes
    from saitenka.app.features.picker import sub_picker
    from saitenka.app.overlay_ids import OverlayId
    from saitenka.app.subselect import SubtitleCandidate
    from saitenka.runtime import events

    class IPC(FakeIPC):
        def command(self, *args):
            if args and args[0] == "set_property":
                self.props[args[1]] = args[2]
            return super().command(*args)

    ipc = IPC()
    ipc.props.update({"path": "/v/Show - 01.mkv", "osd-dimensions": {"w": 1920, "h": 1080}})
    reader = build_session(ipc)
    reader.graph.screen.osd = (1920, 1080)
    downloaded: list[str] = []

    def candidate(item: dict) -> SubtitleCandidate:
        name = item["name"]
        return SubtitleCandidate(
            provider=item.get("provider", "jimaku"),
            name=name,
            size=item.get("size", 1000),
            match=item.get("match", False),
            language=item.get("language"),
            download=lambda: (downloaded.append(name) or f"/tmp/{name}", "ok"),
        )

    def open_picker() -> None:
        reader.graph.picker.store.dispatch(events.PickerOpened())

    def adopt(items=(), warnings=(), error=None) -> None:
        sub_picker.apply_listing(
            reader.graph.picker.store,
            reader.graph.picker.redraw,
            reader.graph.picker.state.generation,
            sub_picker.ListingResult(tuple(items), tuple(warnings), error),
        )

    try:
        op = case["op"]
        if op == "no-provider":
            reader.command(bindings.SUB_PICKER_MSG)
            return {
                "open": reader.graph.picker.state.open,
                "surface": any(
                    command[:2] == ("overlay-add", OverlayId.PICKER) for command in ipc.commands
                ),
            }
        items = [candidate(item) for item in case.get("candidates", [])]
        reader.graph.picker.configure_listing(lambda _video: (items, []))
        open_picker()
        adopt(items, case.get("warnings", []), case.get("error"))
        reader.graph.picker.redraw()
        state = reader.graph.picker.state
        panel = reader.graph.picker.panel
        if op == "listing":
            rows = sub_picker._rows(state)
            return {
                "open": state.open,
                "loading": state.loading,
                "names": [item.name for item in sub_picker.listing_of(state).candidates],
                "rows": [
                    {"text": row.text, "status": row.status, "click": row.click_kind}
                    for row in rows
                ],
                "surface": any(
                    command[:2] == ("overlay-add", OverlayId.PICKER) for command in ipc.commands
                ),
            }
        if op == "message":
            listing = sub_picker.listing_of(state)
            return {
                "warnings": list(listing.warnings),
                "error": listing.error,
                "message": sub_picker._message(state),
            }
        rect = panel.rect
        assert rect is not None
        fetches: list[dict] = []
        original = subtitle_modes.start_fetch
        subtitle_modes.start_fetch = lambda _submit, _get, do, **kwargs: fetches.append(
            {"download": do, "force_select": kwargs["force_select"]}
        )
        try:
            if op in {"click-row", "route-click"}:
                hit = next(
                    box
                    for box in panel.hits
                    if box.kind == "picker-download" and box.value == case.get("index", 0)
                )
                x, y = rect[0] + hit.x + hit.w / 2, rect[1] + hit.y + hit.h / 2
                if op == "route-click":
                    ipc.props["mouse-pos"] = {"hover": True, "x": x, "y": y}
                    reader.command(bindings.CLICK_MSG)
                    captured = True
                else:
                    captured = reader.graph.picker.on_click(
                        reader.graph.interaction.click_target(), x, y
                    )
                if fetches:
                    fetches[0]["download"]()
                return {
                    "captured": captured,
                    "open": reader.graph.picker.state.open,
                    "fetch_count": len(fetches),
                    "force_select": fetches[0]["force_select"] if fetches else None,
                    "downloaded": downloaded,
                }
            if op == "empty-click":
                x, y = (0, 0) if case["where"] == "outside" else (rect[0] + 1, rect[1] + 1)
                captured = reader.graph.picker.on_click(
                    reader.graph.interaction.click_target(), x, y
                )
                return {
                    "captured": captured,
                    "open": reader.graph.picker.state.open,
                    "fetch_count": len(fetches),
                }
            if op == "scroll":
                ipc.props["mouse-pos"] = {"hover": True, "x": 0, "y": 0}
                outside = reader.graph.picker.scroll(reader.graph.interaction.wheel_step(), 1)
                ipc.props["mouse-pos"] = {"hover": True, "x": rect[0] + 5, "y": rect[1] + 5}
                inside = reader.graph.picker.scroll(reader.graph.interaction.wheel_step(), 1)
                return {
                    "outside": outside,
                    "inside": inside,
                    "scroll": reader.graph.picker.state.scroll,
                }
            if op == "suppress-hover":
                ipc.props["mouse-pos"] = {"hover": True, "x": rect[0] + 5, "y": rect[1] + 5}
                inside = reader.graph.picker.suppress_hover(
                    reader.graph.interaction.hover_suppression()
                )
                ipc.props["mouse-pos"] = {"hover": True, "x": 0, "y": 0}
                outside = reader.graph.picker.suppress_hover(
                    reader.graph.interaction.hover_suppression()
                )
                return {"inside": inside, "outside": outside}
        finally:
            subtitle_modes.start_fetch = original
        raise ValueError(f"unknown picker operation: {op!r}")
    finally:
        reader.close()
