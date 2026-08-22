"""The copy-flash pulse: which popup is wearing the "copied" border, if any.

One slot, not one per popup — a second copy supersedes the first, and the border is retired by a
named deadline rather than by the next draw. That deadline is the whole reason this is a decision:
a pulse that cannot be retired is a border stuck on the popup until something happens to redraw it,
which reads as a rendering bug rather than as missing feedback. So the pulse fails *closed* — no
deadline, no border — and whether the deadline took rides on the event, exactly as the sidebar's
manual hold does.

The overlay is an id, not a surface: this decides *which* popup is pulsing, never what is drawn on
it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PulseState:
    """The popup wearing the border, or `None`."""

    overlay: int | None = None


@dataclass(frozen=True, slots=True)
class Repaint:
    """This popup's border changed, so it has to be drawn again."""

    overlay: int


@dataclass(frozen=True, slots=True)
class PulseTurn:
    state: PulseState
    decisions: tuple[Repaint, ...] = ()


def pulsed(state: PulseState, overlay: int, *, armed: bool) -> PulseTurn:
    """Start a pulse on `overlay`. `armed` is whether its expiry deadline took."""
    if not armed:
        return PulseTurn(state)
    return PulseTurn(PulseState(overlay), (Repaint(overlay),))


def expired(state: PulseState) -> PulseTurn:
    """The deadline landed. Nothing pulsing is not an error — a superseded deadline can still be
    the one that fires — so it decides nothing rather than repainting a popup at a stale id."""
    if state.overlay is None:
        return PulseTurn(state)
    return PulseTurn(PulseState(), (Repaint(state.overlay),))
