"""The impure ends of the pointer/tooltip-routing feature, behind `StatelessRouter`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app import interaction_intents, surfaces, tooltip
from saitenka.app.intents import DismissHover

if TYPE_CHECKING:
    from saitenka.app.popups import TipPorts
    from saitenka.app.prefetch import TipScale
    from saitenka.app.surfaces import WheelStep


class InteractionHost(Protocol):
    """This feature's whole host coupling. See `PanelHost` for why it is spelled out."""

    @property
    def tip_can_go_back(self) -> bool: ...

    @property
    def tip_scale(self) -> TipScale: ...

    @property
    def wheel_step(self) -> WheelStep: ...

    @property
    def surface_router(self) -> surfaces.SurfaceRouter: ...

    @property
    def tip_ports(self) -> TipPorts: ...

    def scroll_tip(self, delta: int) -> None: ...

    def retire_hover(self) -> None: ...

    def on_click(self) -> None: ...

    def copy_click(self) -> None: ...


class InteractionAdapter:
    def __init__(self, host: InteractionHost) -> None:
        self._host = host

    def inputs(self) -> interaction_intents.InteractionInputs:
        return interaction_intents.InteractionInputs(
            can_go_back=self._host.tip_can_go_back,
            tooltip_view_height=self._host.tip_scale.ref_h,
        )

    def apply(self, effect: interaction_intents.InteractionEffect, /) -> None:
        host = self._host
        if isinstance(effect, interaction_intents.RouteWheel):
            host.surface_router.route_scroll(host.wheel_step, effect.steps)
        elif isinstance(effect, interaction_intents.ScrollTooltip):
            host.scroll_tip(effect.pixels)
        elif isinstance(effect, interaction_intents.NavigateBack):
            tooltip.tip_back(host.tip_ports)
        elif isinstance(effect, DismissHover):
            host.retire_hover()
        elif isinstance(effect, interaction_intents.RouteClick):
            host.on_click()
        elif isinstance(effect, interaction_intents.CopyUnderCursor):
            host.copy_click()
