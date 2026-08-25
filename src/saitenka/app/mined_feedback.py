"""The ⊕→✓ feedback: refreshing whatever is showing a newly mined word.

Split out of `miner_ui`, which is the card-*preview* surface — a different owner and a different
lifetime. The mining owner writes membership before asking this INTERACTION projection to redraw.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from saitenka.app import nested_popup, tooltip
from saitenka.app.popups import hovered_meta
from saitenka.runtime import events

if TYPE_CHECKING:
    from saitenka.app.popups import HoverInputs, ShowActions, TipPorts
    from saitenka.app.tooltip_panel import PanelPorts


def mark_mined(
    ports: TipPorts,
    panel: PanelPorts,
    inputs: HoverInputs,
    show: ShowActions,
    expression: str,
) -> None:
    """Refresh the shown popups after mining membership has committed."""
    if not expression:
        return
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
