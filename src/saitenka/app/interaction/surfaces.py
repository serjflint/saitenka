"""Feature-facing contracts for owner-thread surface arbitration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.picker.sub_picker import DownloadPorts
    from saitenka.app.features.sidebar.sidebar import SidebarActions, SidebarView
    from saitenka.app.features.tooltip.popups import ClickPorts, HoverInputs, TipPorts
    from saitenka.app.features.tooltip.tooltip_panel import PanelPorts


class SurfaceState(Protocol):
    @property
    def open(self) -> bool: ...


_TIP_WHEEL_FRAC = 0.14


def tip_wheel_pixels(ref_h: int, steps: int) -> int:
    """Convert coalesced wheel notches to the tooltip's pixel unit."""
    return steps * round(ref_h * _TIP_WHEEL_FRAC)


@dataclass(frozen=True, slots=True)
class HoverSuppression:
    pointer: object
    release_hover: Callable[[], None]
    hide_annotation: Callable[[], None]


@dataclass(frozen=True, slots=True)
class WheelStep:
    pointer: object
    sidebar: SidebarView
    hold_sidebar: Callable[[float], bool]
    scroll_tip: Callable[[int], None]
    tip_ref_h: int


@dataclass(frozen=True, slots=True)
class ClickTarget:
    """Per-turn ports for the globally ordered click arbitration."""

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
    """One feature surface's input participation and read-only shown state."""

    name: str
    state_of: Callable[[], SurfaceState]
    suppress_hover: Callable[[HoverSuppression], bool] = _never
    scroll: Callable[[WheelStep, int], bool] = _no_scroll
    on_click: Callable[[ClickTarget, float, float], bool] = _no_click
    click_without_cue: bool = False

    def captures(self) -> bool:
        return self.state_of().open
