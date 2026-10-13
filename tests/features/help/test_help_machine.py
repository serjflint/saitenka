"""The help-overlay machine: a pure function of the state it is given and the document's length."""

from __future__ import annotations

import pytest

from saitenka.runtime.help import (
    CloseHelp,
    HelpCommand,
    HelpState,
    HelpTurn,
    OpenHelp,
    ShowHelpPage,
    decide,
    repaginate,
)

CLOSED = HelpState()


@pytest.mark.parametrize(
    ("given", "command", "expected"),
    [
        (CLOSED, HelpCommand.TOGGLE, HelpTurn(HelpState(open=True), (OpenHelp(),))),
        (HelpState(open=True, page=1), HelpCommand.TOGGLE, HelpTurn(CLOSED, (CloseHelp(),))),
        (HelpState(open=True, page=1), HelpCommand.CLOSE, HelpTurn(CLOSED, (CloseHelp(),))),
        (CLOSED, HelpCommand.CLOSE, HelpTurn(CLOSED)),
    ],
    ids=["toggle-open", "toggle-close", "close-open", "close-closed"],
)
def test_open_and_close_commands_preserve_their_input(
    given: HelpState, command: HelpCommand, expected: HelpTurn
) -> None:
    before = HelpState(given.open, given.page)

    assert decide(given, command) == expected
    assert given == before


@pytest.mark.parametrize(
    ("command", "page", "expected"),
    [
        (HelpCommand.NEXT, 0, 1),
        (HelpCommand.PREVIOUS, 2, 1),
    ],
)
def test_paging_moves_the_state_and_publishes_the_page(
    command: HelpCommand, page: int, expected: int
) -> None:
    given = HelpState(open=True, page=page)

    turn = decide(given, command, page_count=3)

    assert turn == HelpTurn(HelpState(open=True, page=expected), (ShowHelpPage(expected),))
    assert given == HelpState(open=True, page=page)


@pytest.mark.parametrize(
    ("command", "page"),
    [
        (HelpCommand.NEXT, 2),  # already on the last page
        (HelpCommand.PREVIOUS, 0),  # already on the first
    ],
)
def test_paging_clamps_at_both_ends(command: HelpCommand, page: int) -> None:
    state = HelpState(open=True, page=page)
    assert decide(state, command, page_count=3) == HelpTurn(state)


@pytest.mark.parametrize("command", [HelpCommand.NEXT, HelpCommand.PREVIOUS])
def test_paging_a_closed_overlay_decides_nothing(command: HelpCommand) -> None:
    """Help navigation stays eligible while the overlay is shut — the command policy admits it — so
    the machine, not the key routing, is what makes it harmless."""
    assert decide(CLOSED, command, page_count=3) == HelpTurn(CLOSED)


def test_a_shrunk_document_pulls_the_stored_page_back_onto_it() -> None:
    """The correction is state, not drawing. Were it only in the render, the stored page would stay
    past the end and the next PREVIOUS would clamp *forward* — from a page never on screen."""
    given = HelpState(open=True, page=5)

    assert repaginate(given, 3) == HelpTurn(HelpState(open=True, page=2))
    assert given == HelpState(open=True, page=5)
    assert decide(HelpState(open=True, page=2), HelpCommand.PREVIOUS, page_count=3) == HelpTurn(
        HelpState(open=True, page=1), (ShowHelpPage(1),)
    )


def test_repagination_publishes_nothing_and_leaves_a_fitting_page_alone() -> None:
    """It is a correction, not a command: the caller is already drawing, so a decision to draw
    would be a second one. A page that still fits is not touched at all."""
    assert repaginate(HelpState(open=True, page=1), 3) == HelpTurn(HelpState(open=True, page=1))
