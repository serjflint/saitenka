"""The subtitle-navigation settle window as a revision-fenced named deadline (WP4.5).

Right after a manual sub-nav, mpv briefly re-reports two transient `sub-text` values: an empty
blip, and the PRE-nav cue before the seek lands. Adopting either flashes the wrong line and
silently resets the nav index, breaking next/next/next chaining.

The window used to be a wall-clock timestamp compared on every reconcile. It is now an explicit
state with its own revision, so a due event from an older navigation cannot close the window a
newer one opened, and a source replacement or close retires it exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How long mpv is allowed to keep reporting mid-seek transients. Covers a slow seek.
SETTLE_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class NavigationSettleDue:
    """Identity of one settle window; the revision fences a late due."""

    revision: int


@dataclass(frozen=True, slots=True)
class SettleWindow:
    revision: int = 0
    open: bool = False

    def begin(self) -> SettleWindow:
        """Open a window for a new navigation, superseding any window still open."""
        return SettleWindow(self.revision + 1, open=True)

    @property
    def identity(self) -> NavigationSettleDue:
        return NavigationSettleDue(self.revision)

    def retire(self) -> SettleWindow:
        """Close the window. Idempotent: reconcile, source replacement and close all call it."""
        return SettleWindow(self.revision, open=False)

    def due(self, identity: NavigationSettleDue) -> SettleWindow:
        """Apply a due event. A due for a superseded navigation leaves the window alone."""
        if identity.revision != self.revision:
            return self
        return self.retire()


def swallows(
    window: SettleWindow, *, text: str, nav_prev_text: str, identity_reinstall: bool
) -> bool:
    """Whether this observation is a mid-seek transient the open window should absorb."""
    if not window.open:
        return False
    return not text.strip() or (text == nav_prev_text and not identity_reinstall)
