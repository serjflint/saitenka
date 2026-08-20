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

    The rebuild is unconditional; the generation bump is `MinedSet`'s call. Re-mining a word already
    in the deck moves no membership, so the cached panels stay valid — but the user still pressed a
    key and still expects the panel in front of them to redraw.
    """
    if not expression:
        return
    reader.session.mined.add(expression)
    if reader.hover >= 0 and reader.tip.view.state is not None:
        token = reader.tokens[reader.hover]
        if expression in {token.lemma, token.surface}:
            reader.tip.hover = replace(reader.tip.hover, mined=True)
        reader._show_tooltip(reader.hover)  # rebuild the base tooltip (✓ if it's this word)
    if reader.tip.nest.state is not None and reader.tip.nest.token is not None:
        nested_popup.rerender_with_mined_state(reader)
