"""Coalesced geometry-refresh scheduling.

A resize, seek or delay change publishes several observations in one batch, and each one changes a
geometry input. Refreshing per observation would run libass several times for one visual change, so
the refresh is deferred to a named zero-delay deadline: the first change arms it, later changes in
the same batch find it already armed, and it comes due at the head of the next drain — after the
whole batch has been observed.

Supersession is by pipeline generation, reproducing the guard the tick drain used: a refresh armed
before a source/track change must not run against the replacement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime import EffectFinished, EffectOutcome, Owner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.ipc import MpvIPC


GEOMETRY_REFRESH_TIMER = "subtitle:geometry-refresh"


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


class GeometryRefreshController:
    """Own coalescing and retirement of geometry refresh deadlines."""

    def __init__(
        self,
        ipc: MpvIPC,
        *,
        generation: Callable[[], int],
        refresh: Callable[[], None],
    ) -> None:
        self._ipc = ipc
        self._generation = generation
        self._refresh = refresh
        self._window = RefreshWindow()

    def arm(self) -> None:
        window, due = self._window.arm(self._generation())
        self._window = window
        if due is None:
            return

        def fired(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._fire(due)

        if self._ipc.schedule_runtime_timer(
            owner=Owner.SUBTITLE,
            identity=due,
            timer=GEOMETRY_REFRESH_TIMER,
            due_at=time.monotonic(),
            on_finished=fired,
        ):
            return
        self._window = self._window.retire()
        self._refresh()

    def _fire(self, due: GeometryRefreshDue) -> None:
        if not self._window.fires(due, self._generation()):
            return
        self._window = self._window.retire()
        self._refresh()

    def retire(self) -> None:
        if self._window.armed is None:
            return
        self._window = self._window.retire()
        self._ipc.cancel_runtime_timer(GEOMETRY_REFRESH_TIMER)
