"""Pure reducer for the panel commands.

The panels — sidebar, episode analysis, subtitle picker, card preview — are each a surface the user
opens and closes. The decision is thin, and the reason to state it anyway is that the one piece of
policy they *do* differ on was scattered: whether opening a panel dismisses the tooltip floating
over the video. Collected here it is a table, and the inconsistency in it is visible rather than
three modules deep.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saitenka.app.intents import DismissHover


class Panel(StrEnum):
    SIDEBAR = "sidebar"
    ANALYSIS = "analysis"
    SUBTITLE_PICKER = "subtitle-picker"
    CARD_PREVIEW = "card-preview"


class PanelCommand(StrEnum):
    """The wire names this reducer owns."""

    TOGGLE_SIDEBAR = "toggle-sidebar"
    TOGGLE_ANALYSIS = "toggle-analysis"
    TOGGLE_SUBTITLE_PICKER = "toggle-subtitle-picker"
    REPLAY_CARD_PREVIEW = "replay-card-preview"
    CLOSE_CARD_PREVIEW = "close-card-preview"


#: Panels whose opening dismisses a shown tooltip; centred modal panels do not share this policy.
_DISMISSES_HOVER = frozenset({Panel.SIDEBAR})


@dataclass(frozen=True, slots=True)
class PanelInputs:
    """Every fact the panel commands decide from, read once before deciding."""

    open_panels: frozenset[Panel] = frozenset()


@dataclass(frozen=True, slots=True)
class OpenPanel:
    panel: Panel


@dataclass(frozen=True, slots=True)
class ClosePanel:
    panel: Panel


@dataclass(frozen=True, slots=True)
class ReplayCardPreview:
    """Re-show the last mined card's preview; the host owns whether there is one."""


type PanelEffect = OpenPanel | ClosePanel | DismissHover | ReplayCardPreview


def _toggle(panel: Panel):
    def decide(inputs: PanelInputs) -> tuple[PanelEffect, ...]:
        if panel in inputs.open_panels:
            return (ClosePanel(panel),)
        if panel in _DISMISSES_HOVER:
            # Before the open, so the tooltip is gone by the time the panel paints over where it
            # was — the other order leaves one frame of tooltip on top of the panel.
            return (DismissHover(), OpenPanel(panel))
        return (OpenPanel(panel),)

    return decide


_REDUCERS = {
    PanelCommand.TOGGLE_SIDEBAR: _toggle(Panel.SIDEBAR),
    PanelCommand.TOGGLE_ANALYSIS: _toggle(Panel.ANALYSIS),
    PanelCommand.TOGGLE_SUBTITLE_PICKER: _toggle(Panel.SUBTITLE_PICKER),
    PanelCommand.REPLAY_CARD_PREVIEW: lambda _inputs: (ReplayCardPreview(),),
    PanelCommand.CLOSE_CARD_PREVIEW: lambda _inputs: (ClosePanel(Panel.CARD_PREVIEW),),
}


def reduce(command: PanelCommand, inputs: PanelInputs) -> tuple[PanelEffect, ...]:
    """Decide one panel command."""
    return _REDUCERS[command](inputs)
