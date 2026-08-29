"""The impure ends of the pointer/tooltip-routing feature, behind `StatelessRouter`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.intents import DismissHover
from saitenka.app.interaction.surfaces import ClickTarget, HoverSuppression, WheelStep
from saitenka.app.session import interaction_intents

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.picker.sub_picker import DownloadPorts
    from saitenka.app.features.sidebar.sidebar import SidebarActions
    from saitenka.app.features.sidebar.sidebar_controller import SidebarController
    from saitenka.app.features.tooltip.navigation_endpoint import TooltipNavigationEndpoint
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.session.surfaces import SurfaceRouter


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


@dataclass(frozen=True, slots=True)
class InteractionPorts:
    """Fresh cross-widget capabilities used during one owner-thread turn."""

    overlay_visible: Callable[[], bool]
    playback: PlaybackObservationController
    router: SurfaceRouter
    tooltip: TooltipController
    sidebar: SidebarController
    download: Callable[[], DownloadPorts]
    sidebar_actions: Callable[[], SidebarActions]
    hide_annotation: Callable[[], None]
    settle_annotation: Callable[[], None]
    sync_mouse_capture: Callable[[], None]


class InteractionCoordinator:
    """Own global surface arbitration and one-turn cross-widget settlement."""

    def __init__(self, ports: InteractionPorts) -> None:
        self._ports = ports

    @property
    def router(self) -> SurfaceRouter:
        return self._ports.router

    def wants_mouse_capture(self) -> bool:
        return self._ports.router.wants_mouse_capture()

    def hover_suppression(self) -> HoverSuppression:
        ports = self._ports
        return HoverSuppression(
            ports.playback.value("mouse-pos"),
            ports.tooltip.retire_hover,
            ports.hide_annotation,
        )

    def wheel_step(self) -> WheelStep:
        ports = self._ports
        return WheelStep(
            ports.playback.value("mouse-pos"),
            ports.sidebar.view(),
            ports.sidebar.hold_scroll,
            ports.tooltip.scroll_tip,
            ports.tooltip.scale().ref_h,
        )

    def click_target(self) -> ClickTarget:
        ports = self._ports
        tooltip = ports.tooltip
        return ClickTarget(
            ports.download(),
            ports.sidebar.view(),
            ports.sidebar_actions(),
            tooltip.tip_ports,
            tooltip.panel_ports,
            tooltip.click_ports,
            tooltip.hover_inputs,
        )

    def sidebar_actions(self) -> SidebarActions:
        return self._ports.sidebar_actions()

    def update_hover(self) -> None:
        ports = self._ports
        if not ports.overlay_visible() or ports.router.suppress_hover(self.hover_suppression()):
            return
        ports.tooltip.update_hover()

    def route_click(self) -> None:
        ports = self._ports
        if not ports.overlay_visible():
            return
        pointer = ports.playback.mapping("mouse-pos")
        ports.router.route_click(
            self.click_target(),
            pointer.get("x", -1),
            pointer.get("y", -1),
        )

    def route_wheel(self, steps: int) -> None:
        self._ports.router.route_scroll(self.wheel_step(), steps)

    def settle(self) -> None:
        ports = self._ports
        ports.settle_annotation()
        ports.sync_mouse_capture()
        ports.tooltip.publish_pending()
        ports.tooltip.update_prefetch()
        ports.sidebar.follow()

    def command_coordinator(self) -> InteractionCommandCoordinator:
        tooltip = self._ports.tooltip
        return InteractionCommandCoordinator(
            InteractionCommandPorts(
                navigation=tooltip.navigation_endpoint,
                route_wheel=self.route_wheel,
                scroll_tip=tooltip.scroll_tip,
                retire_hover=tooltip.retire_hover,
                route_click=self.route_click,
                copy_click=tooltip.copy_click,
            )
        )
