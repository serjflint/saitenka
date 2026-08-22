"""The subtitle picker's machine: whether it is up, which listing it is on, and where it is scrolled.

The generation is the whole reason this is a machine rather than five fields. A listing runs off the
reader thread and comes back whenever it comes back; the only thing that can say whether it still
belongs to the picker on screen is a number that moved when the picker did. Open and close both bump
it, so a result for a picker the user has since closed *and reopened* is dropped rather than
repopulating a list they are no longer looking at.

What it deliberately does not hold: the drawn rectangle and its hit boxes. Those are what one paint
put on one screen, and folding a per-paint observation into a session-lived slot is the lifetime
mistake the geometry split already made once. The app keeps them beside the panel that produced them.

The listing itself is carried opaquely — `runtime` cannot name a subtitle candidate, and it has no
reason to: the machine decides *whether* a result is still wanted, never what is in it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Rows one wheel notch moves. Here rather than in the surface module because the clamp that uses it
#: is the machine's, and a step size the caller could pick is a second policy.
ROWS_PER_WHEEL_STEP = 3


@dataclass(frozen=True, slots=True)
class PickerState:
    """Window 1's state: up or not, which listing generation, how far down, and what arrived."""

    open: bool = False
    generation: int = 0
    scroll: int = 0
    #: A listing is in flight. Distinct from `listing is None`, which is also true of a listing that
    #: came back empty — one draws a spinner and the other says there is nothing.
    loading: bool = False
    listing: object | None = None


@dataclass(frozen=True, slots=True)
class PickerRetired:
    """The picker was up and is now down — so the caller has an overlay to take off the screen.

    Published rather than returned as a bool because closing an already-closed picker must not
    remove an overlay somebody else has since put at that id.
    """


@dataclass(frozen=True, slots=True)
class ListingAdopted:
    """A result was still current and is now the picker's. The caller repaints on this and only this."""


@dataclass(frozen=True, slots=True)
class PickerTurn:
    state: PickerState
    decisions: tuple[PickerRetired | ListingAdopted, ...] = ()


def opened(state: PickerState) -> PickerTurn:
    """Put the picker up on a fresh generation, empty and loading."""
    return PickerTurn(PickerState(open=True, generation=state.generation + 1, loading=True))


def retired(state: PickerState) -> PickerTurn:
    """Take the picker down, invalidating whatever listing is still in flight for it."""
    if not state.open:
        return PickerTurn(state)
    return PickerTurn(PickerState(generation=state.generation + 1), (PickerRetired(),))


def listed(state: PickerState, generation: int, listing: object) -> PickerTurn:
    """Install a result if it still belongs to the picker on screen.

    The guard is the staleness rule, and it is here rather than at the call site so that a stale
    result leaves the state *untouched* — not merely skips a repaint, which is what a caller
    checking before redrawing would give.
    """
    if not state.open or generation != state.generation:
        return PickerTurn(state)
    return PickerTurn(replace(state, loading=False, listing=listing), (ListingAdopted(),))


def scrolled(state: PickerState, steps: int, count: int) -> PickerTurn:
    """Move the scroll by whole notches, clamped onto a list of `count` rows."""
    return PickerTurn(replace(state, scroll=clamp_scroll(state.scroll, steps, count)))


def clamp_scroll(scroll: int, steps: int, count: int) -> int:
    """Where a wheel notch leaves the picker. Clamped at both ends rather than wrapped: a list that
    jumped from the last row back to the first on one more notch reads as a lost scroll position."""
    return max(0, min(max(0, count - 1), scroll + steps * ROWS_PER_WHEEL_STEP))
