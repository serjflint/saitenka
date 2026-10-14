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


@pytest.mark.parametrize(
    ("command", "open_panels", "expected"),
    [
        (
            PanelCommand.TOGGLE_SIDEBAR,
            frozenset(),
            (DismissHover(), OpenPanel(Panel.SIDEBAR)),
        ),
        (
            PanelCommand.TOGGLE_SIDEBAR,
            frozenset({Panel.SIDEBAR}),
            (ClosePanel(Panel.SIDEBAR),),
        ),
        (
            PanelCommand.TOGGLE_ANALYSIS,
            frozenset({Panel.SIDEBAR}),
            (OpenPanel(Panel.ANALYSIS),),
        ),
        (
            PanelCommand.TOGGLE_ANALYSIS,
            frozenset({Panel.ANALYSIS}),
            (ClosePanel(Panel.ANALYSIS),),
        ),
        (
            PanelCommand.TOGGLE_SUBTITLE_PICKER,
            frozenset({Panel.ANALYSIS}),
            (OpenPanel(Panel.SUBTITLE_PICKER),),
        ),
        (
            PanelCommand.TOGGLE_SUBTITLE_PICKER,
            frozenset({Panel.SUBTITLE_PICKER}),
            (ClosePanel(Panel.SUBTITLE_PICKER),),
        ),
        (PanelCommand.REPLAY_CARD_PREVIEW, frozenset(), (ReplayCardPreview(),)),
        (
            PanelCommand.CLOSE_CARD_PREVIEW,
            frozenset(),
            (ClosePanel(Panel.CARD_PREVIEW),),
        ),
    ],
)
def test_panel_command_matrix(command, open_panels, expected) -> None:
    assert reduce(command, PanelInputs(open_panels)) == expected
