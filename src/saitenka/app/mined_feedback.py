"""The ⊕→✓ feedback: recording a word as in-deck, and refreshing whatever is showing it.

Split out of `miner_ui`, which is the card-*preview* surface — a different owner and a different
lifetime. This module writes one SESSION fact (the mined set) and asks the INTERACTION surfaces
currently on screen to redraw; the preview panel is not one of them and never was.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from saitenka.app import nested_popup

if TYPE_CHECKING:
    from saitenka.app.controller import Reader


def mark_mined(reader: Reader, expression: str) -> None:
    """Record a word as in-deck and refresh the shown popups so their ⊕ flips to ✓ immediately.

    The refresh is unconditional but the generation bump is not: re-mining a word already in the
    deck (the duplicate path) leaves membership where it was, so every panel cached against it is
    still correct. What the user asked for is the *visible* rebuild below, which happens either way.
    """
    if not expression:
        return
    reader._mined.add(expression)
    if reader.hover >= 0 and reader._tip_state is not None:
        token = reader.tokens[reader.hover]
        if expression in {token.lemma, token.surface}:
            reader._hover_meta = replace(reader._hover_meta, mined=True)
        reader._show_tooltip(reader.hover)  # rebuild the base tooltip (✓ if it's this word)
    if reader._nest.state is not None and reader._nest.token is not None:
        nested_popup.rerender_with_mined_state(reader)
