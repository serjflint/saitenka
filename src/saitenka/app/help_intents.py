"""Pure reducer for the help-overlay commands (WP5.3 of the runtime migration).

The reducer receives an immutable snapshot of the facts a decision needs and returns typed effects.
It performs no I/O, holds no state and never sees `Reader`; the executor in `help_overlay` gathers
the inputs and carries the effects out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HelpCommand(StrEnum):
    """The wire names this reducer owns. Each maps to exactly one intent."""

    TOGGLE = "toggle"
    PREVIOUS = "previous"
    NEXT = "next"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class HelpInputs:
    """Every fact the help commands decide from, read once before deciding."""

    open: bool
    page: int = 0
    #: Pages in the rendered document. Zero is "open but nothing to page through", a different
    #: state from closed.
    page_count: int = 0


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


def _paged(inputs: HelpInputs, delta: int) -> tuple[HelpEffect, ...]:
    if not inputs.open:
        return ()
    # No separate empty-document arm: the lower clamp already collapses a zero page count onto the
    # current page, so the step decides nothing. A guard here read as load-bearing and was not —
    # removing it changed no test, which is how it was caught.
    target = max(0, min(inputs.page_count - 1, inputs.page + delta))
    return () if target == inputs.page else (ShowHelpPage(target),)


_REDUCERS = {
    HelpCommand.TOGGLE: lambda inputs: (CloseHelp(),) if inputs.open else (OpenHelp(),),
    HelpCommand.CLOSE: lambda inputs: (CloseHelp(),) if inputs.open else (),
    HelpCommand.PREVIOUS: lambda inputs: _paged(inputs, -1),
    HelpCommand.NEXT: lambda inputs: _paged(inputs, 1),
}


def reduce(command: HelpCommand, inputs: HelpInputs) -> tuple[HelpEffect, ...]:
    """Decide one help command.

    Unlike the subtitle reducer, an empty result is a legitimate answer here: paging a closed
    overlay, or past its last page, is nothing the user should be told about. It is not a *silent*
    no-op — the command still terminates with an outcome in the runtime's command ledger — so the
    distinction the migration cares about is preserved without inventing a toast for a stray key.
    """
    return _REDUCERS[command](inputs)
