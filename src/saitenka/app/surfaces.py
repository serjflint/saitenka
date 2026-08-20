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

    from saitenka.app.controller import Reader
    from saitenka.app.reader_context import InteractionContext


class SurfaceState(Protocol):
    """What every surface's state object exposes to the registry: ``open`` — is it shown right now.
    Each state (``HelpState``/``PickerState``/``SidebarState`` as a field, ``PreviewState``/
    ``TooltipState`` as a derived property) satisfies this, so mouse-capture reads one uniform predicate."""

    @property
    def open(self) -> bool:
        """Is this surface shown right now. Read-only here (a settable ``open`` field satisfies it too)."""
        ...


# Wheel-scroll amount the tooltip (the terminal fallback surface) applies per coalesced step — the OSD
# tooltip's own step, distinct from the keyboard TIP_UP/DOWN 0.12 in controller._HANDLERS.
_TIP_WHEEL_FRAC = 0.14


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


def _never(_suppression: HoverSuppression) -> bool:
    return False


def _no_scroll(_reader: Reader, _steps: int) -> bool:
    return False


def _no_click(_reader: Reader, _x: float, _y: float) -> bool:
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
    scroll: Callable[[Reader, int], bool] = _no_scroll
    on_click: Callable[[Reader, float, float], bool] = _no_click

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


def _tip_click(reader: Reader, _x: float, _y: float) -> bool:
    """Terminal click handler: preview/nested/tip together (tooltip.on_click reads mouse-pos itself and
    routes preview→nested→tip with its diagnostic log). Returns True so routing stops here."""
    tooltip.on_click(reader)
    return True


def _tip_scroll(reader: Reader, steps: int) -> bool:
    """Terminal wheel fallback: scroll the tooltip. Always claims the step (matches the old else-branch)."""
    reader._scroll_tip(steps * round(reader._tip_ref_h * _TIP_WHEEL_FRAC))
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


def hover_suppression(reader: Reader) -> HoverSuppression:
    """Snapshot the host into the port the hooks take.

    The one host-taking row left in this chain, and deliberately so — it is the seam, the same shape
    as `build_draw_request`. Named rather than inlined because a hook's own unit test needs to build
    the same value production does; a test that assembles it by hand is a second definition of the
    port that drifts from this one.
    """
    return HoverSuppression(
        reader.interaction,
        reader._prop("mouse-pos"),
        reader.retire_hover,
        lambda: reader.set_annotation_hover(revealed=False),
    )


def suppress_hover(reader: Reader) -> bool:
    """First surface (topmost-first) that swallows the hover under the cursor."""
    suppression = hover_suppression(reader)
    return any(s.suppress_hover(suppression) for s in SURFACES)


def route_scroll(reader: Reader, steps: int) -> bool:
    """Deliver a coalesced wheel step to the topmost surface that claims it."""
    return any(s.scroll(reader, steps) for s in SURFACES)


def route_click(reader: Reader, x: float, y: float) -> bool:
    """Deliver a left-click to the topmost surface that claims it."""
    return any(s.on_click(reader, x, y) for s in SURFACES)
