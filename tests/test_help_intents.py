"""WP5.3: the help-overlay decision is a pure function of the facts it reads."""

from __future__ import annotations

import pytest

from saitenka.app.help_intents import (
    CloseHelp,
    HelpCommand,
    HelpInputs,
    OpenHelp,
    ShowHelpPage,
    reduce,
)

OPEN = HelpInputs(open=True, page=1, page_count=3)
CLOSED = HelpInputs(open=False)


def test_toggling_opens_a_closed_overlay_at_the_first_page() -> None:
    assert reduce(HelpCommand.TOGGLE, CLOSED) == (OpenHelp(0),)


def test_toggling_closes_an_open_overlay() -> None:
    assert reduce(HelpCommand.TOGGLE, OPEN) == (CloseHelp(),)


def test_closing_a_closed_overlay_decides_nothing() -> None:
    assert reduce(HelpCommand.CLOSE, CLOSED) == ()


@pytest.mark.parametrize(
    ("command", "page", "expected"),
    [
        (HelpCommand.NEXT, 0, (ShowHelpPage(1),)),
        (HelpCommand.NEXT, 2, ()),  # already on the last page
        (HelpCommand.PREVIOUS, 2, (ShowHelpPage(1),)),
        (HelpCommand.PREVIOUS, 0, ()),  # already on the first
    ],
)
def test_paging_clamps_at_both_ends(command: HelpCommand, page: int, expected: tuple) -> None:
    assert reduce(command, HelpInputs(open=True, page=page, page_count=3)) == expected


@pytest.mark.parametrize("command", [HelpCommand.NEXT, HelpCommand.PREVIOUS])
def test_paging_a_closed_overlay_decides_nothing(command: HelpCommand) -> None:
    """Help navigation stays eligible while the overlay is shut — the command policy admits it — so
    the reducer, not the key routing, is what makes it harmless."""
    assert reduce(command, CLOSED) == ()


@pytest.mark.parametrize("command", [HelpCommand.NEXT, HelpCommand.PREVIOUS])
def test_paging_an_empty_document_decides_nothing(command: HelpCommand) -> None:
    """An open overlay with no pages is not the closed case. Nothing special handles it: the lower
    clamp collapses the step onto the current page, so the decision is empty by construction."""
    assert reduce(command, HelpInputs(open=True, page=0, page_count=0)) == ()


def test_the_reducer_reads_its_inputs_without_mutating_them() -> None:
    given = HelpInputs(open=True, page=1, page_count=3)

    for command in HelpCommand:
        reduce(command, given)

    assert given == HelpInputs(open=True, page=1, page_count=3)
