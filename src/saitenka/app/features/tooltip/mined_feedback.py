"""Refresh visible tooltip projections after mining membership changes."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from saitenka.app.features.tooltip import nested_popup, tooltip
from saitenka.app.features.tooltip.popups import hovered_meta
from saitenka.runtime import events

if TYPE_CHECKING:
    from saitenka.app.features.tooltip.popups import HoverInputs, ShowActions, TipPorts
    from saitenka.app.features.tooltip.tooltip_panel import PanelPorts


def mark_mined(
    ports: TipPorts,
    panel: PanelPorts,
    inputs: HoverInputs,
    show: ShowActions,
    expression: str,
) -> None:
    """Refresh shown popups after mining membership commits."""
    if not expression:
        return
    hovered = inputs.hover()
    if hovered >= 0 and ports.tip.view.state is not None:
        token = inputs.tokens[hovered]
        if expression in {token.lemma, token.surface}:
            revised = replace(hovered_meta(ports.word_store), mined=True)
            ports.word_store.dispatch(events.HoverWordResolved(revised, revised=True))
        tooltip.show_tooltip(ports, panel, inputs, show, hovered)
    if ports.tip.nest.state is not None and ports.tip.nest.token is not None:
        nested_popup.rerender_with_mined_state(ports, panel)
