"""Tooltip navigation assembled from its owner and presentation resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.tooltip import prefetch, tooltip
from saitenka.app.features.tooltip.tooltip_controller import TooltipPresentation

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.help.help_controller import ScreenState
    from saitenka.app.features.tooltip.popups import PopupView, TipPorts
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.features.tooltip.tooltip_engaged import EngagedRequest
    from saitenka.app.interaction.presentation import InteractionSurfaces
    from saitenka.app.render_cache import LoadedView
    from saitenka.app.toast_controller import NotificationSink


@dataclass(frozen=True, slots=True)
class TooltipNavigationEndpoint:
    tooltip: TooltipController
    screen: ScreenState
    surfaces: InteractionSurfaces
    tip_scale_override: float
    tip_max_frac: float
    nested_max_frac: float
    request_render_ahead: Callable[[PopupView, int], bool]
    peek_render_cache: Callable[[object], LoadedView | None]
    schedule_flash_expiry: Callable[[], bool]
    notifications: NotificationSink
    request_engaged_tooltip: Callable[[EngagedRequest], bool]

    def scale(self) -> prefetch.TipScale:
        return prefetch.tip_scale(
            self.screen.osd[1],
            override=self.tip_scale_override,
            max_frac=self.tip_max_frac,
        )

    def ports(self) -> TipPorts:
        owner = self.tooltip
        return owner.build_tip_ports(
            TooltipPresentation(
                scale=self.scale(),
                surfaces=self.surfaces,
                request_render_ahead=self.request_render_ahead,
                osd=self.screen.osd,
                nested_max_frac=self.nested_max_frac,
                peek_render_cache=self.peek_render_cache,
                schedule_flash_expiry=self.schedule_flash_expiry,
                toast=self.notifications.show,
                request_engaged_tooltip=self.request_engaged_tooltip,
            )
        )

    def can_go_back(self) -> bool:
        return self.tooltip.observation().can_go_back

    def back(self) -> None:
        tooltip.tip_back(self.ports())
