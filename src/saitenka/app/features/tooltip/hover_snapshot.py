"""A read-only snapshot of Tooltip's hover stack.

The owner returns frozen copies of the values at call time, never its live mutable containers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.features.tooltip.popups import Panel
    from saitenka.app.tokenize import Token


@dataclass(frozen=True)
class NestedView:
    """The nested scan popup (a tooltip opened by hovering a word inside another tooltip)."""

    state: Panel | None
    word: str | None
    token: Token | None
    key: tuple | None
    rect: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class TipView:
    """The base tooltip."""

    state: Panel | None
    key: object | None
    rect: tuple[int, int, int, int] | None
    hide_pending: bool  # a linger deadline is armed


@dataclass(frozen=True)
class HoverView:
    nested: NestedView
    tip: TipView
    paused: bool  # playback auto-paused by the tooltip
    scan_target: str | None  # scan-cell tail the cursor is settling on


def snapshot(
    nest,
    tip: TipView,
    *,
    paused: bool,
    scan_target: str | None,
) -> HoverView:
    """Frozen point-in-time view of the hover stack."""
    return HoverView(
        nested=NestedView(
            state=nest.state, word=nest.word, token=nest.token, key=nest.key, rect=nest.rect
        ),
        tip=tip,
        paused=paused,
        scan_target=scan_target,
    )
