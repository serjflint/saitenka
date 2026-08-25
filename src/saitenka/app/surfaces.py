"""Closed OSD-surface routing with explicit topmost-first product order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app import sidebar, sub_picker, tooltip

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.help_controller import HelpController
    from saitenka.app.popups import ClickPorts, HoverInputs, TipPorts
    from saitenka.app.reader_context import InteractionContext
    from saitenka.app.sidebar import SidebarActions, SidebarView
    from saitenka.app.tooltip_panel import PanelPorts


class SurfaceState(Protocol):
    """What every surface's state object exposes to the registry: ``open`` — is it shown right now.
    Every surface's state is now a slice feature reached through `InteractionContext`, and each
    answers this the same way — as a field or as a derived property — so mouse-capture reads one
    uniform predicate and cannot tell them apart."""

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
    """What a surface needs to decide whether it swallows the hover under the cursor.

    Four members, cut by owner rather than assembled by call chain: the INTERACTION state the surface
    reads, the pointer it hit-tests against, and the two teardowns it performs when it claims the
    pointer. In the target `release_hover` is an event the tooltip feature reduces — this is the port
    that lets the hook stop taking the host before that exists.
    """

    interaction: InteractionContext
    pointer: object
    release_hover: Callable[[], None]
    hide_annotation: Callable[[], None]


@dataclass(frozen=True, slots=True)
class WheelStep:
    """What a surface needs to decide whether it claims a coalesced wheel step.

    Cut by owner, like `HoverSuppression`: the INTERACTION state the surfaces read, the pointer they
    hit-test against, and the one act each performs once it claims the step. A surface that pages on
    the wheel (help) carries the page act, not the command that builds it — which page a step means
    is the surface's own arithmetic, not the router's.
    """

    interaction: InteractionContext
    pointer: object
    page_help: Callable[[int], None]
    redraw_picker: Callable[[], None]
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

    interaction: InteractionContext
    download: sub_picker.DownloadPorts
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
    interaction: InteractionContext,
) -> SurfaceRouter:
    """Combine one feature-owned row with the explicit legacy surface residue."""
    return SurfaceRouter(
        (
            help_controller.surface_binding(),
            SurfaceSpec(
                "sub_picker",
                state_of=interaction.sub_picker_surface_state,
                suppress_hover=sub_picker.suppress_hover,
                scroll=sub_picker.scroll,
                on_click=sub_picker.on_click,
            ),
            SurfaceSpec(
                "sidebar",
                state_of=interaction.sidebar_surface_state,
                suppress_hover=sidebar.suppress_hover,
                scroll=sidebar.scroll,
                on_click=sidebar.on_click,
            ),
            SurfaceSpec("preview", state_of=interaction.preview_surface_state),
            SurfaceSpec(
                "tooltip",
                state_of=interaction.tooltip_surface_state,
                on_click=_tip_click,
                scroll=_tip_scroll,
            ),
        )
    )
