"""Coalesced geometry-refresh scheduling (WP4.4).

A resize, seek or delay change publishes several observations in one batch, and each one changes a
geometry input. Refreshing per observation would run libass several times for one visual change, so
the refresh is deferred to a named zero-delay deadline: the first change arms it, later changes in
the same batch find it already armed, and it comes due at the head of the next drain — after the
whole batch has been observed.

Supersession is by pipeline generation, reproducing the guard the tick drain used: a refresh armed
before a source/track change must not run against the replacement.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeometryRefreshDue:
    """The identity of one armed refresh. Compared whole, so a late due is rejected by value."""

    generation: int


@dataclass(frozen=True, slots=True)
class RefreshWindow:
    """Whether a refresh is armed, and for which generation."""

    armed: GeometryRefreshDue | None = None

    def arm(self, generation: int) -> tuple[RefreshWindow, GeometryRefreshDue | None]:
        """Arm for `generation`, or coalesce into the pending one.

        Returns the new window and the identity that needs scheduling — None when an existing
        deadline already covers this change, which is the whole point of the window.
        """
        if self.armed is not None and self.armed.generation == generation:
            return self, None
        due = GeometryRefreshDue(generation)
        return RefreshWindow(due), due

    def fires(self, due: GeometryRefreshDue, generation: int) -> bool:
        """Whether this due event should still refresh: it must be the armed one, and its
        generation must not have moved under it."""
        return self.armed == due and due.generation == generation

    def retire(self) -> RefreshWindow:
        return RefreshWindow()
