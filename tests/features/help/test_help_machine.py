"""The help-overlay machine: a pure function of the state it is given and the document's length."""

from __future__ import annotations

import pytest

from saitenka.runtime.help import (
    HelpCommand,
    HelpState,
    HelpTurn,
    ShowHelpPage,
    decide,
    repaginate,
)

CLOSED = HelpState()


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
