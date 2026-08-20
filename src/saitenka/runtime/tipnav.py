"""The base tooltip's link-navigation back-stack: how deep it is, and what Esc goes back to.

Clicking a cross-reference replaces the tooltip's content in place and pushes what was showing; Esc
pops it. That is a machine rather than a list because the pop *refuses* — an empty stack means the
key falls through to whatever else Esc does — and spelled inline the fact and the act were one
statement (`if not nav: return False` then `nav.pop()`), so nothing could ask how deep the stack was
without being the code that unwound it.

The captured view rides opaquely. `runtime` cannot name a panel, a pixmap or a token, and has no
reason to: this decides *whether* there is a step to go back to, never what is in one. Capturing and
restoring stay app-side, which is also what keeps the back-stack from being a second copy of the
view it snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TipNavState:
    """The stack, oldest first. Empty is the common case — most tooltips are never navigated."""

    back: tuple[object, ...] = ()

    @property
    def can_go_back(self) -> bool:
        return bool(self.back)


@dataclass(frozen=True, slots=True)
class TipViewRestored:
    """A step was popped, and this is what was under it. Published rather than returned, so the
    caller that puts the view back is not also the code that decided there was one."""

    view: object


@dataclass(frozen=True, slots=True)
class TipNavTurn:
    state: TipNavState
    decisions: tuple[TipViewRestored, ...] = ()


def pushed(state: TipNavState, view: object) -> TipNavTurn:
    """A navigation is about to replace the content: keep what it replaces."""
    return TipNavTurn(TipNavState((*state.back, view)))


def popped(state: TipNavState) -> TipNavTurn:
    """Go back one step, if there is one."""
    if not state.back:
        return TipNavTurn(state)
    return TipNavTurn(TipNavState(state.back[:-1]), (TipViewRestored(state.back[-1]),))


def cleared(state: TipNavState) -> TipNavTurn:
    """The tooltip this history belonged to is gone. Nothing is restored — the views it held
    described a word that is no longer hovered, and going "back" to one would reopen it."""
    return TipNavTurn(TipNavState()) if state.back else TipNavTurn(state)
