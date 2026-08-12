"""A read-only snapshot of the Reader's hover stack — the public observation seam (#43).

Tests assert against ``reader.hover_view()`` instead of reaching into ``_nest`` / ``_tip_*`` /
``_paused_by_tip`` / ``_nav_idx`` / ``_scan_target``, so a behaviour-preserving reshape of that state
(rename a field, fold the tip state into an object) no longer breaks the suite. The views are frozen
copies of the values at call time, never the live mutable objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.controller import Reader
    from saitenka.app.popups import Panel
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
    hide_at: float


@dataclass(frozen=True)
class HoverView:
    nested: NestedView
    tip: TipView
    paused: bool  # playback auto-paused by the tooltip
    nav_idx: int  # last sub-nav cue index jumped to (-1 = unknown)
    scan_target: str | None  # scan-cell tail the cursor is settling on


def snapshot(reader: Reader) -> HoverView:
    """Frozen point-in-time view of ``reader``'s hover stack."""
    n = reader._nest
    return HoverView(
        nested=NestedView(state=n.state, word=n.word, token=n.token, key=n.key, rect=n.rect),
        tip=TipView(
            state=reader._tip_state,
            key=reader._tip_key,
            rect=reader._tip_rect,
            hide_at=reader._hide_at,
        ),
        paused=reader._paused_by_tip,
        nav_idx=reader._nav_idx,
        scan_target=reader._scan_target,
    )
