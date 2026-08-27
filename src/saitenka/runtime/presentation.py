"""`Owner.PRESENTATION`'s state: what Saitenka has decided to put on screen.

Deliberately not here: whether the overlay is visible at all. That is the mpv `Overlay`'s own
state, and a copy in this slot would be a second representation of one fact rather than a slice of
it — the reveal asks the overlay, exactly as it does now.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranslationState:
    """The secondary (translation) line.

    Two facts, and not interchangeable: `held` is the manual toggle the user set, `drawn` is what
    is on screen right now. An auto-reveal ending must not release a track the manual toggle is
    still holding, and that decision is `held` alone — which is why an auto reveal moves `drawn`
    and never `held`.
    """

    held: bool = False
    drawn: str | None = None
