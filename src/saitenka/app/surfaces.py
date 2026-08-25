"""Closed OSD-surface routing with explicit topmost-first product order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app import tooltip

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.help_controller import HelpController
    from saitenka.app.picker_controller import PickerController
    from saitenka.app.popups import ClickPorts, HoverInputs, TipPorts
    from saitenka.app.preview_controller import PreviewController
    from saitenka.app.sidebar import SidebarActions, SidebarView
    from saitenka.app.sidebar_controller import SidebarController
    from saitenka.app.sub_picker import DownloadPorts
    from saitenka.app.tooltip_controller import TooltipController
    from saitenka.app.tooltip_panel import PanelPorts


class SurfaceState(Protocol):
    """The one fact every feature-owned surface exposes to input arbitration."""

    @property
    def open(self) -> bool:
        """Is this surface shown right now. Read-only here (a settable ``open`` field satisfies it too)."""
        ...


# Wheel-scroll amount the tooltip (the terminal fallback surface) applies per coalesced step — the OSD
# tooltip's own step, distinct from the keyboard TIP_UP/DOWN 0.12 in the command table.
_TIP_WHEEL_FRAC = 0.14


def tip_wheel_pixels(ref_h: int, steps: int) -> int:
    """Pixels the tooltip scrolls for `steps` coalesced wheel notches.

    A notch is the unit the input path speaks and pixels are the unit `scroll_tip` takes, so the
    conversion needs one home: a second copy is a fake that scrolls a different amount than the
    wheel it stands in for, and it reads as a production regression from the assertion's side.
    """
    return steps * round(ref_h * _TIP_WHEEL_FRAC)


@dataclass(frozen=True, slots=True)
class HoverSuppression:
    """Pointer facts and acts available to a surface that suppresses hover."""

    pointer: object
    release_hover: Callable[[], None]
    hide_annotation: Callable[[], None]


@dataclass(frozen=True, slots=True)
class WheelStep:
    """Per-turn facts and acts available to a surface that claims wheel input."""

    pointer: object
    sidebar: SidebarView
    hold_sidebar: Callable[[float], bool]
    scroll_tip: Callable[[int], None]
    #: The reference panel height the tooltip's per-step fraction is taken against.
    tip_ref_h: int


@dataclass(frozen=True, slots=True)
class ClickTarget:
    """What a surface needs to decide whether it claims a left-click.

    The tooltip's four values pass through whole rather than being flattened: the terminal handler
    hands them straight to `tooltip.on_click`, and unpacking them here would make this the union of
    the tooltip's features instead of a router's port.
    """

    download: DownloadPorts
    sidebar: SidebarView
    sidebar_acts: SidebarActions
    tip: TipPorts
    panel: PanelPorts
    click: ClickPorts
    hover: HoverInputs


def _never(_suppression: HoverSuppression) -> bool:
    return False


def _no_scroll(_wheel: WheelStep, _steps: int) -> bool:
    return False


def _no_click(_target: ClickTarget, _x: float, _y: float) -> bool:
    return False


@dataclass(frozen=True)
class SurfaceSpec:
    """One OSD surface's participation in the input chains. ``state_of`` returns the surface's state
    object (a :class:`SurfaceState`); ``captures`` derives shown-ness from its uniform ``open``. Unset
    interaction predicates default to no-op, so a capture-only surface (``preview``) or a click-less one
    (``help``) states only what it handles."""

    name: str
    state_of: Callable[[], SurfaceState]
    suppress_hover: Callable[[HoverSuppression], bool] = _never
    scroll: Callable[[WheelStep, int], bool] = _no_scroll
    on_click: Callable[[ClickTarget, float, float], bool] = _no_click

    def captures(self) -> bool:
        return self.state_of().open


def _tip_click(target: ClickTarget, _x: float, _y: float) -> bool:
    """Terminal click handler: preview/nested/tip together (tooltip.on_click reads mouse-pos itself and
    routes preview→nested→tip with its diagnostic log). Returns True so routing stops here."""
    tooltip.on_click(target.tip, target.panel, target.click, target.hover)
    return True


def _tip_scroll(wheel: WheelStep, steps: int) -> bool:
    """Terminal wheel fallback: scroll the tooltip. Always claims the step (matches the old else-branch)."""
    wheel.scroll_tip(tip_wheel_pixels(wheel.tip_ref_h, steps))
    return True


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

    def route_click(self, target: ClickTarget, x: float, y: float) -> bool:
        return any(spec.on_click(target, x, y) for spec in self.specs)


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
