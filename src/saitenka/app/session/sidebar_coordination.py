"""Cross-feature turns initiated by the sidebar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.preview.preparation import PreviewDestination

if TYPE_CHECKING:
    from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
    from saitenka.app.features.sidebar.sidebar import SidebarActions, SidebarView


@dataclass(frozen=True, slots=True)
class MinedPreviewCard:
    """Offline fallback fields for a mined note preview."""

    expression: str
    reading: str
    glosses: tuple[str, ...] = ()


def open_mined(
    view: SidebarView,
    actions: SidebarActions,
    preview: PreviewCommandEndpoint,
    note_id: int,
) -> None:
    """Seek to a mined cue and show its retained Anki note when available."""
    card = view.mined().by_note_id(note_id)
    if card is None:
        return
    actions.seek("sidebar-seek-mined", card.cue_start)
    if view.can_mine:
        preview.preview.present_existing(
            preview.ports,
            lambda: preview.card_source(at=card.cue_start),
            preview.toast,
            note_id,
            MinedPreviewCard(card.expression, card.reading),
            "exists",
            enabled=True,
            destination=PreviewDestination(card.cue_start, card.cue_end, preview.playback.cue.text),
        )
