"""The shortcut-overlay machine: what it shows, and what a command decides to do about it.

Lives in `runtime/` because it names its own vocabulary — a command, a page count, three decisions —
and imports nothing from `app`. That is the whole admission test: the machine says *what to do*, the
app says which overlay id to draw on and which key context to hand back.

An empty decision set is a legitimate answer. Paging a closed overlay, or past its last page, is
nothing the user should be told about; the command still terminates with an outcome in the runtime's
command ledger, so a stray key is not a *silent* no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class HelpCommand(StrEnum):
    """The wire names this machine owns. Each maps to exactly one intent."""

    TOGGLE = "toggle"
    PREVIOUS = "previous"
    NEXT = "next"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class HelpState:
    """In-player shortcut-reference overlay: whether it is showing, and which page."""

    open: bool = False
    page: int = 0


@dataclass(frozen=True, slots=True)
class OpenHelp:
    """Show the overlay at ``page``, taking the help key context."""

    page: int = 0


@dataclass(frozen=True, slots=True)
class CloseHelp:
    """Remove the overlay and give the key context back."""


@dataclass(frozen=True, slots=True)
class ShowHelpPage:
    index: int


type HelpEffect = OpenHelp | CloseHelp | ShowHelpPage


@dataclass(frozen=True, slots=True)
class HelpTurn:
    """One command's outcome: the state it leaves, and what the caller has to perform."""

    state: HelpState
    decisions: tuple[HelpEffect, ...] = ()


def _paged(state: HelpState, page_count: int, delta: int) -> HelpTurn:
    if not state.open:
        return HelpTurn(state)
    # No separate empty-document arm: the lower clamp already collapses a zero page count onto the
    # current page, so the step decides nothing. A guard here read as load-bearing and was not —
    # removing it changed no test, which is how it was caught.
    target = max(0, min(page_count - 1, state.page + delta))
    if target == state.page:
        return HelpTurn(state)
    return HelpTurn(replace(state, page=target), (ShowHelpPage(target),))


def repaginate(state: HelpState, page_count: int) -> HelpTurn:
    """Fold a freshly measured document length back into the state, clamping the page onto it.

    Its own entry rather than a step inside the render: a screen resize can shrink the reference
    below the page being shown, and if the correction lived only in the drawing then the *stored*
    page would stay past the end — so the next PREVIOUS would clamp forward instead of stepping
    back, from a page the user was never on.
    """
    target = max(0, min(page_count - 1, state.page))
    if not state.open or target == state.page:
        return HelpTurn(state)
    return HelpTurn(replace(state, page=target))


def decide(state: HelpState, command: HelpCommand, *, page_count: int = 0) -> HelpTurn:
    """Decide one help command against the overlay's current state.

    `page_count` is the rendered document's length, which only the open arms read — a keypress the
    closed arm discards must not cost a document build, so the caller measures it lazily.
    """
    match command:
        case HelpCommand.TOGGLE if state.open:
            return HelpTurn(HelpState(), (CloseHelp(),))
        case HelpCommand.TOGGLE:
            return HelpTurn(HelpState(open=True), (OpenHelp(),))
        case HelpCommand.CLOSE:
            return HelpTurn(HelpState(), (CloseHelp(),)) if state.open else HelpTurn(state)
        case HelpCommand.PREVIOUS:
            return _paged(state, page_count, -1)
        case HelpCommand.NEXT:
            return _paged(state, page_count, 1)
