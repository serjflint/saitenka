"""WP5.3: opening and closing a panel is a decision, and hover dismissal is part of it."""

from __future__ import annotations

import pytest

from saitenka.app.session.panel_intents import (
    ClosePanel,
    DismissHover,
    OpenPanel,
    Panel,
    PanelCommand,
    PanelInputs,
    ReplayCardPreview,
    reduce,
)

_TOGGLES = {
    PanelCommand.TOGGLE_SIDEBAR: Panel.SIDEBAR,
    PanelCommand.TOGGLE_ANALYSIS: Panel.ANALYSIS,
    PanelCommand.TOGGLE_SUBTITLE_PICKER: Panel.SUBTITLE_PICKER,
}


@pytest.mark.parametrize(("command", "panel"), list(_TOGGLES.items()))
def test_an_open_panel_closes(command: PanelCommand, panel: Panel) -> None:
    assert reduce(command, PanelInputs(frozenset({panel}))) == (ClosePanel(panel),)


@pytest.mark.parametrize(("command", "panel"), list(_TOGGLES.items()))
def test_a_toggle_reads_only_its_own_panel(command: PanelCommand, panel: Panel) -> None:
    """Every other panel being open must not change the answer — a toggle is about one surface."""
    others = frozenset(_TOGGLES.values()) - {panel}

    assert reduce(command, PanelInputs(others))[-1] == OpenPanel(panel)


def test_opening_the_sidebar_dismisses_the_tooltip_first() -> None:
    """Order matters: dismissing after the open leaves one frame of tooltip painted over the panel."""
    assert reduce(PanelCommand.TOGGLE_SIDEBAR, PanelInputs()) == (
        DismissHover(),
        OpenPanel(Panel.SIDEBAR),
    )


@pytest.mark.parametrize("panel", [Panel.ANALYSIS, Panel.SUBTITLE_PICKER])
def test_the_centred_modals_leave_the_tooltip_alone(panel: Panel) -> None:
    """Pins today's behaviour, and it looks like an oversight rather than a decision — a tooltip
    can float over either of these. Recorded here so changing it is a deliberate act with a
    failing test, not something this migration did in passing.
    """
    command = next(c for c, p in _TOGGLES.items() if p is panel)

    assert reduce(command, PanelInputs()) == (OpenPanel(panel),)


def test_closing_the_sidebar_does_not_touch_the_tooltip() -> None:
    """The dismissal belongs to the open, not to the panel: closing reveals the video again."""
    assert reduce(PanelCommand.TOGGLE_SIDEBAR, PanelInputs(frozenset({Panel.SIDEBAR}))) == (
        ClosePanel(Panel.SIDEBAR),
    )


def test_the_card_preview_replays_and_closes_without_a_toggle() -> None:
    """It is not a toggle: replay re-shows the last mined card whether or not one is up, and the
    host owns whether there is one to show."""
    assert reduce(PanelCommand.REPLAY_CARD_PREVIEW, PanelInputs()) == (ReplayCardPreview(),)
    assert reduce(PanelCommand.CLOSE_CARD_PREVIEW, PanelInputs()) == (
        ClosePanel(Panel.CARD_PREVIEW),
    )


def test_the_reducer_reads_its_inputs_without_mutating_them() -> None:
    given = PanelInputs(frozenset({Panel.SIDEBAR}))

    for command in PanelCommand:
        reduce(command, given)

    assert given == PanelInputs(frozenset({Panel.SIDEBAR}))
