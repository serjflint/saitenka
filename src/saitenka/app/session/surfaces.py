"""Global OSD-surface order and owner-thread input routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.interaction.surfaces import (
    ClickTarget,
    HoverSuppression,
    SurfaceSpec,
    WheelStep,
    tip_wheel_pixels,
)

if TYPE_CHECKING:
    from saitenka.app.features.help.help_controller import HelpController
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.features.sidebar.sidebar_controller import SidebarController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController

SURFACE_ORDER = ("help", "sub_picker", "sidebar", "preview", "tooltip")


class SurfaceRouter:
    def __init__(
        self,
        specs: tuple[SurfaceSpec, ...],
        *,
        order: tuple[str, ...] = SURFACE_ORDER,
    ) -> None:
        names = tuple(spec.name for spec in specs)
        if len(names) != len(set(names)):
            raise ValueError("surface names must be unique")
        if names != order:
            raise ValueError(f"surface order mismatch: {names!r} != {order!r}")
        self.specs = specs

    def wants_mouse_capture(self) -> bool:
        return any(spec.captures() for spec in self.specs)

    def suppress_hover(self, suppression: HoverSuppression) -> bool:
        return any(spec.suppress_hover(suppression) for spec in self.specs)

    def route_scroll(self, wheel: WheelStep, steps: int) -> bool:
        return any(spec.scroll(wheel, steps) for spec in self.specs)

    def route_click(
        self,
        target: ClickTarget,
        x: float,
        y: float,
        *,
        cue_active: bool = True,
    ) -> bool:
        return any(
            spec.on_click(target, x, y)
            for spec in self.specs
            if cue_active or spec.click_without_cue
        )


def build_surface_router(
    help_controller: HelpController,
    picker_controller: PickerController,
    sidebar_controller: SidebarController,
    preview_controller: PreviewController,
    tooltip_controller: TooltipController,
) -> SurfaceRouter:
    """Assemble feature-owned rows under the explicit global z-order."""
    return SurfaceRouter(
        (
            help_controller.surface_binding(),
            picker_controller.surface_binding(),
            sidebar_controller.surface_binding(),
            preview_controller.surface_binding(),
            tooltip_controller.surface_binding(),
        )
    )


__all__ = [
    "SURFACE_ORDER",
    "ClickTarget",
    "HoverSuppression",
    "SurfaceRouter",
    "SurfaceSpec",
    "WheelStep",
    "build_surface_router",
    "tip_wheel_pixels",
]
