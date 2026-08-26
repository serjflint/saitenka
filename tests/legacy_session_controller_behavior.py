"""Legacy SessionController adapter for the implementation-neutral behavior trace."""

from __future__ import annotations

from typing import TYPE_CHECKING

from runtime_behavior import BehaviorRecord, BehaviorTrace, CueState, InteractionState, PixelState

from saitenka.app.subtitle_render import SUB_ID

if TYPE_CHECKING:
    from saitenka.app.session.controller import SessionController


def _visible_surfaces(commands: list[tuple]) -> set[object]:
    visible: set[object] = set()
    for command in commands:
        if not command:
            continue
        if command[0] == "overlay-add" and len(command) > 1:
            visible.add(command[1])
        elif command[0] == "overlay-remove" and len(command) > 1:
            visible.discard(command[1])
        elif command[0] == "osd-overlay" and len(command) > 2:
            if command[2] == "none":
                visible.discard(command[1])
            else:
                visible.add(command[1])
    return visible


def _cue(reader: SessionController) -> CueState:
    if reader.annotation_controller.view.retired and reader._cue_identity_ever_installed:
        return "retired"
    if reader.annotation_controller.view.identity is not None or reader.sub_text.strip():
        return "active"
    return "none"


def _interaction(reader: SessionController) -> InteractionState:
    if reader.tip.view.rect is not None:
        return "tooltip"
    if reader.tooltip_controller.selected >= 0 and reader.boxes:
        return "hovered"
    return "ready" if reader.boxes else "unavailable"


def _pixels(reader: SessionController, visible: set[object]) -> PixelState:
    if SUB_ID in visible:
        return "legacy"
    if reader.ipc.props.get("sub-visibility") is True:
        return "native"
    renderer = reader.subtitle_pipeline.renderer
    ownership = getattr(renderer, "ownership_state", None)
    return "unknown" if ownership is not None and ownership.owner.value == "unknown" else "none"


class LegacyReaderTrace:
    def __init__(self, reader: SessionController) -> None:
        self.reader = reader
        self.trace = BehaviorTrace()

    def observe(self, event: str, *, outcome: str) -> None:
        visible = _visible_surfaces(self.reader.ipc.commands)
        self.trace.append(
            BehaviorRecord(
                event=event,
                cue=_cue(self.reader),
                pixels=_pixels(self.reader, visible),
                interaction=_interaction(self.reader),
                surfaces="present" if visible else "none",
                lifecycle="closed" if self.reader._stop.is_set() else "open",
                outcome=outcome,
            )
        )

    def records(self) -> tuple[dict[str, str], ...]:
        return self.trace.records()
