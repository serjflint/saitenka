"""Ordered OSD-surface registry — the one place that knows which overlays exist and their z-order.

The controller's four input chains (forced-mouse capture, hover suppression, wheel scroll, click
routing) used to hand-list each surface, so a new surface had to be wired into all four by hand — miss
one and it silently doesn't occlude (exactly the #100 picker click-through bug). Here each surface is a
:class:`SurfaceSpec` — a state accessor (shown-ness via the uniform ``open``) plus the hover/scroll/click
predicates it handles; the chains iterate this one tuple.

``SURFACES`` is TOPMOST-FIRST: the first surface whose predicate claims a click/scroll wins. The order
is an explicit, reviewable tuple (not ``__init_subclass__`` auto-registration, whose order would follow
import order — the very implicitness that hid the bug). Adding a surface = adding one row here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app import help_overlay, sidebar, sub_picker, tooltip

if TYPE_CHECKING:
    from collections.abc import Callable

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
    #: The surface's state object (has `.open`), reached from the INTERACTION context that owns all
    #: five — not from the host. This is the member that made every accessor a host-taking row.
    state_of: Callable[[InteractionContext], SurfaceState]
    suppress_hover: Callable[[HoverSuppression], bool] = _never
    scroll: Callable[[WheelStep, int], bool] = _no_scroll
    on_click: Callable[[ClickTarget, float, float], bool] = _no_click

    def captures(self, interaction: InteractionContext) -> bool:
        """Shown → owns the forced mouse section this tick (_wants_mouse_capture)."""
        return self.state_of(interaction).open


def _help_state(interaction: InteractionContext) -> SurfaceState:
    return interaction.help


def _picker_state(interaction: InteractionContext) -> SurfaceState:
    return interaction.sub_picker


def _sidebar_state(interaction: InteractionContext) -> SurfaceState:
    return interaction.sidebar


def _preview_state(interaction: InteractionContext) -> SurfaceState:
    return interaction.preview


def _tip_state(interaction: InteractionContext) -> SurfaceState:
    return interaction.tip


def _tip_click(target: ClickTarget, _x: float, _y: float) -> bool:
    """Terminal click handler: preview/nested/tip together (tooltip.on_click reads mouse-pos itself and
    routes preview→nested→tip with its diagnostic log). Returns True so routing stops here."""
    tooltip.on_click(target.tip, target.panel, target.click, target.hover)
    return True


def _tip_scroll(wheel: WheelStep, steps: int) -> bool:
    """Terminal wheel fallback: scroll the tooltip. Always claims the step (matches the old else-branch)."""
    wheel.scroll_tip(tip_wheel_pixels(wheel.tip_ref_h, steps))
    return True


# Topmost-first. help captures all scroll/hover while open (and _handle blocks non-help clicks upstream,
# so it needs no on_click); preview is capture-only — its click stays inside the tooltip handler.
SURFACES: tuple[SurfaceSpec, ...] = (
    SurfaceSpec(
        "help",
        state_of=_help_state,
        suppress_hover=help_overlay.suppress_hover,
        scroll=help_overlay.scroll,
    ),
    SurfaceSpec(
        "sub_picker",
        state_of=_picker_state,
        suppress_hover=sub_picker.suppress_hover,
        scroll=sub_picker.scroll,
        on_click=sub_picker.on_click,
    ),
    SurfaceSpec(
        "sidebar",
        state_of=_sidebar_state,
        suppress_hover=sidebar.suppress_hover,
        scroll=sidebar.scroll,
        on_click=sidebar.on_click,
    ),
    SurfaceSpec("preview", state_of=_preview_state),
    SurfaceSpec(
        "tooltip",
        state_of=_tip_state,
        on_click=_tip_click,
        scroll=_tip_scroll,
    ),
)


def wants_mouse_capture(interaction: InteractionContext) -> bool:
    """Any surface shown → own the forced mouse section this tick (occlusion)."""
    return any(s.captures(interaction) for s in SURFACES)


def suppress_hover(suppression: HoverSuppression) -> bool:
    """First surface (topmost-first) that swallows the hover under the cursor."""
    return any(s.suppress_hover(suppression) for s in SURFACES)


def route_scroll(wheel: WheelStep, steps: int) -> bool:
    """Deliver a coalesced wheel step to the topmost surface that claims it."""
    return any(s.scroll(wheel, steps) for s in SURFACES)


def route_click(target: ClickTarget, x: float, y: float) -> bool:
    """Deliver a left-click to the topmost surface that claims it."""
    return any(s.on_click(target, x, y) for s in SURFACES)
