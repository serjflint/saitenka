"""The ⊕→✓ feedback: recording a word as in-deck, and refreshing whatever is showing it.

Split out of `miner_ui`, which is the card-*preview* surface — a different owner and a different
lifetime. This module writes one SESSION fact (the mined set) and asks the INTERACTION surfaces
currently on screen to redraw; the preview panel is not one of them and never was.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from saitenka.app import nested_popup, tooltip
from saitenka.app.popups import hovered_meta
from saitenka.runtime import events

if TYPE_CHECKING:
    from saitenka.app.popups import HoverInputs, ShowActions, TipPorts, WordLookup
    from saitenka.app.tooltip_panel import PanelPorts


def mark_mined(
    ports: TipPorts,
    panel: PanelPorts,
    lookup: WordLookup,
    inputs: HoverInputs,
    show: ShowActions,
    expression: str,
) -> None:
    """Record a word as in-deck and refresh the shown popups so their ⊕ flips to ✓ immediately.

    The rebuild is unconditional; the generation bump is `MinedSet`'s call. Re-mining a word already
    in the deck moves no membership, so the cached panels stay valid — but the user still pressed a
    key and still expects the panel in front of them to redraw.
    """
    if not expression:
        return
    lookup.mined.add(expression)
    hovered = inputs.hover()
    if hovered >= 0 and ports.tip.view.state is not None:
        token = inputs.tokens[hovered]
        if expression in {token.lemma, token.surface}:
            revised = replace(hovered_meta(ports.word_store), mined=True)
            ports.word_store.dispatch(events.HoverWordResolved(revised, revised=True))
        # Re-SHOW, not re-hover: the word did not change, and `set_hover` would return early.
        tooltip.show_tooltip(ports, panel, inputs, show, hovered)
    if ports.tip.nest.state is not None and ports.tip.nest.token is not None:
        nested_popup.rerender_with_mined_state(ports, panel)
