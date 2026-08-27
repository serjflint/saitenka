"""A read-only snapshot of Tooltip's hover stack.

The owner returns frozen copies of the values at call time, never its live mutable containers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.features.tooltip.tooltip_panel import PanelKey
    from saitenka.model import LinkBox


@dataclass(frozen=True)
class NestedView:
    """The nested scan popup (a tooltip opened by hovering a word inside another tooltip)."""

    shown: bool
    word: str | None
    has_token: bool
    key: tuple | None
    rect: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class TipView:
    """The base tooltip."""

    shown: bool
    panel_id: int | None
    full_height: int
    links: tuple[LinkBox, ...]
    key: PanelKey | None
    rect: tuple[int, int, int, int] | None
    xy: tuple[int, int]
    hide_pending: bool  # a linger deadline is armed


@dataclass(frozen=True)
class HoverView:
    nested: NestedView
    tip: TipView
    paused: bool  # playback auto-paused by the tooltip
    scan_target: str | None  # scan-cell tail the cursor is settling on


def snapshot(
    nest,
    tip,
    *,
    paused: bool,
    scan_target: str | None,
    hide_pending: bool,
) -> HoverView:
    """Frozen point-in-time view of the hover stack."""
    nested_panel = nest.state
    base_panel = tip.state
    return HoverView(
        nested=NestedView(
            shown=nested_panel is not None,
            word=nest.word,
            has_token=nest.token is not None,
            key=nest.key,
            rect=nest.rect,
        ),
        tip=TipView(
            shown=base_panel is not None,
            panel_id=id(base_panel) if base_panel is not None else None,
            full_height=base_panel.full_height if base_panel is not None else 0,
            links=tuple(base_panel.windowed.link_boxes()) if base_panel is not None else (),
            key=tip.key,
            rect=tip.rect,
            xy=tip.xy,
            hide_pending=hide_pending,
        ),
        paused=paused,
        scan_target=scan_target,
    )
