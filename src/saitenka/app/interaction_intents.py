"""Pure reducer for the pointer and tooltip-navigation commands.

Most of what these commands "decide" is really routing — which surface claims a wheel step, which
popup a click lands in — and that already lives in `surfaces.SURFACES`, a topmost-first table. What
is left over, and what this reducer owns, is the part that is genuinely a decision: how far a step
scrolls, and whether Escape walks back through link history or closes the tooltip.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saitenka.app.intents import DismissHover


class InteractionCommand(StrEnum):
    """The wire names this reducer owns."""

    WHEEL_UP = "wheel-up"
    WHEEL_DOWN = "wheel-down"
    TOOLTIP_UP = "tooltip-up"
    TOOLTIP_DOWN = "tooltip-down"
    #: Escape: back through link history first, close only at the root.
    TOOLTIP_BACK_OR_CLOSE = "tooltip-back-or-close"
    CLICK = "click"
    COPY_UNDER_CURSOR = "copy-under-cursor"


@dataclass(frozen=True, slots=True)
class InteractionInputs:
    """Every fact these commands decide from, read once before deciding."""

    #: A link-navigation step to pop. False means Escape closes instead of stepping back.
    can_go_back: bool = False
    #: Height of the tooltip's reference viewport, in reference px. The scroll step is a fraction of
    #: it rather than a pixel constant, so a taller panel pages proportionally.
    tooltip_view_height: int = 0


@dataclass(frozen=True, slots=True)
class RouteWheel:
    """Hand a wheel step to the topmost surface that claims it."""

    steps: int


@dataclass(frozen=True, slots=True)
class ScrollTooltip:
    """Scroll the base tooltip by ``pixels`` (negative is up)."""

    pixels: int


@dataclass(frozen=True, slots=True)
class NavigateBack:
    """Pop one link-navigation step, restoring the previous entry."""


@dataclass(frozen=True, slots=True)
class RouteClick:
    """Deliver a click to the surface under the cursor."""


@dataclass(frozen=True, slots=True)
class CopyUnderCursor:
    pass


type InteractionEffect = (
    RouteWheel | ScrollTooltip | NavigateBack | RouteClick | CopyUnderCursor | DismissHover
)

#: One keyboard step scrolls this fraction of the tooltip's viewport.
_TOOLTIP_STEP = 0.12


def _tooltip_scroll(direction: int):
    def decide(inputs: InteractionInputs) -> tuple[InteractionEffect, ...]:
        return (ScrollTooltip(round(inputs.tooltip_view_height * _TOOLTIP_STEP) * direction),)

    return decide


def _back_or_close(inputs: InteractionInputs) -> tuple[InteractionEffect, ...]:
    # Browser-back-then-close, the feel Yomitan's history gives: Escape unwinds the links you
    # followed one at a time, and only dismisses once there is nothing left to go back to.
    return (NavigateBack(),) if inputs.can_go_back else (DismissHover(),)


_REDUCERS = {
    InteractionCommand.WHEEL_UP: lambda _inputs: (RouteWheel(-1),),
    InteractionCommand.WHEEL_DOWN: lambda _inputs: (RouteWheel(1),),
    InteractionCommand.TOOLTIP_UP: _tooltip_scroll(-1),
    InteractionCommand.TOOLTIP_DOWN: _tooltip_scroll(1),
    InteractionCommand.TOOLTIP_BACK_OR_CLOSE: _back_or_close,
    InteractionCommand.CLICK: lambda _inputs: (RouteClick(),),
    InteractionCommand.COPY_UNDER_CURSOR: lambda _inputs: (CopyUnderCursor(),),
}


def reduce(command: InteractionCommand, inputs: InteractionInputs) -> tuple[InteractionEffect, ...]:
    """Decide one pointer or tooltip-navigation command."""
    return _REDUCERS[command](inputs)
