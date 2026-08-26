"""The impure ends of the pointer/tooltip-routing feature, behind `StatelessRouter`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.intents import DismissHover
from saitenka.app.session import interaction_intents

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.tooltip.navigation_endpoint import TooltipNavigationEndpoint


@dataclass(frozen=True, slots=True)
class InteractionCommandPorts:
    """Surface arbitration and tooltip-navigation authorities."""

    navigation: TooltipNavigationEndpoint
    route_wheel: Callable[[int], None]
    scroll_tip: Callable[[int], None]
    retire_hover: Callable[[], None]
    route_click: Callable[[], None]
    copy_click: Callable[[], None]


class InteractionCommandCoordinator:
    """Coordinate pointer commands across surface routing and tooltip navigation."""

    def __init__(self, ports: InteractionCommandPorts) -> None:
        self._ports = ports

    def inputs(self) -> interaction_intents.InteractionInputs:
        return interaction_intents.InteractionInputs(
            can_go_back=self._ports.navigation.can_go_back(),
            tooltip_view_height=self._ports.navigation.scale().ref_h,
        )

    def apply(self, effect: interaction_intents.InteractionEffect, /) -> None:
        ports = self._ports
        if isinstance(effect, interaction_intents.RouteWheel):
            ports.route_wheel(effect.steps)
        elif isinstance(effect, interaction_intents.ScrollTooltip):
            ports.scroll_tip(effect.pixels)
        elif isinstance(effect, interaction_intents.NavigateBack):
            ports.navigation.back()
        elif isinstance(effect, DismissHover):
            ports.retire_hover()
        elif isinstance(effect, interaction_intents.RouteClick):
            ports.route_click()
        elif isinstance(effect, interaction_intents.CopyUnderCursor):
            ports.copy_click()
