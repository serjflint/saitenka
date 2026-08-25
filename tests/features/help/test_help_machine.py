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

OPEN = HelpState(open=True, page=1)
CLOSED = HelpState()


def test_toggling_opens_a_closed_overlay_at_the_first_page() -> None:
    assert decide(CLOSED, HelpCommand.TOGGLE) == HelpTurn(HelpState(open=True), (OpenHelp(),))


def test_toggling_closes_an_open_overlay_and_forgets_the_page() -> None:
    """The page resets with the close. Reopening onto page 3 of a reference the user shut is a
    surprise, and it is the state — not the caller — that has to guarantee it does not happen."""
    assert decide(OPEN, HelpCommand.TOGGLE) == HelpTurn(HelpState(), (CloseHelp(),))


def test_closing_a_closed_overlay_decides_nothing() -> None:
    assert decide(CLOSED, HelpCommand.CLOSE) == HelpTurn(CLOSED)


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
    turn = decide(HelpState(open=True, page=page), command, page_count=3)
    assert turn == HelpTurn(HelpState(open=True, page=expected), (ShowHelpPage(expected),))


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


@pytest.mark.parametrize("command", [HelpCommand.NEXT, HelpCommand.PREVIOUS])
def test_paging_an_empty_document_decides_nothing(command: HelpCommand) -> None:
    """An open overlay with no pages is not the closed case. Nothing special handles it: the lower
    clamp collapses the step onto the current page, so the decision is empty by construction."""
    assert decide(HelpState(open=True), command, page_count=0) == HelpTurn(HelpState(open=True))


def test_a_shrunk_document_pulls_the_stored_page_back_onto_it() -> None:
    """The correction is state, not drawing. Were it only in the render, the stored page would stay
    past the end and the next PREVIOUS would clamp *forward* — from a page never on screen."""
    assert repaginate(HelpState(open=True, page=5), 3) == HelpTurn(HelpState(open=True, page=2))
    assert decide(HelpState(open=True, page=2), HelpCommand.PREVIOUS, page_count=3) == HelpTurn(
        HelpState(open=True, page=1), (ShowHelpPage(1),)
    )


def test_repagination_publishes_nothing_and_leaves_a_fitting_page_alone() -> None:
    """It is a correction, not a command: the caller is already drawing, so a decision to draw
    would be a second one. A page that still fits is not touched at all."""
    assert repaginate(HelpState(open=True, page=1), 3) == HelpTurn(HelpState(open=True, page=1))
    assert repaginate(CLOSED, 0) == HelpTurn(CLOSED)


def test_deciding_reads_its_state_without_mutating_it() -> None:
    given = HelpState(open=True, page=1)

    for command in HelpCommand:
        decide(given, command, page_count=3)

    assert given == HelpState(open=True, page=1)
