"""The sidebar's machine: which view it shows, where it is scrolled, and whether to follow playback.

Auto-follow is the whole reason this is a machine. Three facts argue over the scroll position — the
active cue moving, the user's wheel, and a redraw against a different screen — and the rule that
settles them is not obvious: a manual scroll wins until the active row is visible again *or* the
thing being rendered changes underneath it. Spelled inline that rule read as four `if`s in a draw
path, and the case it gets wrong (a hold that outlives the episode it was taken during) is invisible
until an episode advances.

What it does not hold: the drawn rectangle, its hit boxes, the row total that draw measured, and the
style memo. Those are one paint's output. The app keeps them beside the panel that produced them —
the same cut the picker makes, and for the same reason.

`geometry` is an opaque identity token, not a size. The machine only ever asks whether it changed;
what makes two renders "the same render" is the app's judgement, and naming its parts here would put
the screen, the cue index and the scorer into `runtime`'s vocabulary for no decision's sake.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Rows one wheel notch moves, and how long a manual scroll suppresses auto-follow for.
ROWS_PER_WHEEL_STEP = 3
MANUAL_SCROLL_HOLD = 1.5

#: The view the sidebar opens on. A string rather than an enum because it is also the name the
#: renderer and the `view:` hit kind spell, and three spellings of one word is how they drift.
TRACK_VIEW = "track"


@dataclass(frozen=True, slots=True)
class SidebarState:
    """Up or not, which view, where scrolled, and what the last follow decision was made against."""

    open: bool = False
    view: str = TRACK_VIEW
    scroll: int = 0
    #: Honour the user's manual scroll over auto-follow until its deadline lands. A flag, not a
    #: timestamp: the deadline owns when the hold ends, so nothing here reads a clock.
    manual_hold: bool = False
    last_active: int = -1
    geometry: object | None = None


@dataclass(frozen=True, slots=True)
class Redraw:
    """Something the user would see moved. The only decision this machine publishes."""


@dataclass(frozen=True, slots=True)
class SidebarTurn:
    state: SidebarState
    decisions: tuple[Redraw, ...] = ()


def _centred(active: int, capacity: int) -> int:
    return max(0, active - capacity // 2)


def shown(state: SidebarState, active: int, capacity: int) -> SidebarTurn:
    """Open the sidebar centred on the active row, and draw it."""
    opened = replace(state, open=True, scroll=_centred(active, capacity), last_active=active)
    return SidebarTurn(opened, (Redraw(),))


def hidden(state: SidebarState) -> SidebarTurn:
    return SidebarTurn(replace(state, open=False))


def reindexed(state: SidebarState) -> SidebarTurn:
    """A new cue index: the scroll and the follow anchor describe rows that no longer exist."""
    return SidebarTurn(replace(state, scroll=0, last_active=-1), (Redraw(),))


def view_selected(state: SidebarState, view: str) -> SidebarTurn:
    """Switch views, back to the top — a scroll offset means nothing in a list of other things."""
    return SidebarTurn(replace(state, view=view, scroll=0))


def scrolled(state: SidebarState, steps: int, maximum: int, *, held: bool) -> SidebarTurn:
    """Move by whole notches and take the manual hold.

    `held` is whether the hold's deadline was actually armed, and it is passed in rather than
    assumed: a hold that can never be released would suppress auto-follow for the rest of the
    session, which is worse than a manual scroll the next cue scrolls away from.
    """
    scroll = max(0, min(maximum, state.scroll + steps * ROWS_PER_WHEEL_STEP))
    return SidebarTurn(replace(state, scroll=scroll, manual_hold=held), (Redraw(),))


def followed(state: SidebarState, active: int, capacity: int, geometry: object) -> SidebarTurn:
    """Re-centre the track view on the active row, unless the manual hold says otherwise.

    The hold is dropped — not merely overridden — whenever the re-centre happens, so a scroll the
    user took two cues ago cannot keep suppressing a follow after the view has caught up with them.
    """
    if not state.open or state.view != TRACK_VIEW:
        return SidebarTurn(state)
    changed = active != state.last_active or geometry != state.geometry
    if not changed and state.manual_hold:
        return SidebarTurn(state)
    scroll, hold = state.scroll, state.manual_hold
    visible = state.scroll <= active < state.scroll + capacity
    if active >= 0 and (not state.manual_hold or visible):
        scroll, hold = _centred(active, capacity), False
    moved = replace(state, scroll=scroll, manual_hold=hold, last_active=active, geometry=geometry)
    redraw = (Redraw(),) if changed or scroll != state.scroll else ()
    return SidebarTurn(moved, redraw)


def released(state: SidebarState) -> SidebarTurn:
    """The manual hold's deadline landed. Auto-follow resumes at the next cue, not this instant —
    yanking the list out from under the pointer the moment a timer fires is the jarring version."""
    return SidebarTurn(replace(state, manual_hold=False))
