"""Adapt owner-routed reducers to the session reactor.

An event outside the declared routing vocabulary is inert but counted. This keeps extension and
raw diagnostic events from ending the live session while making every unhandled kind observable.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from saitenka.runtime.effects import Owner
from saitenka.runtime.state import RoutedEvent, RouteError

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.diagnostics import RuntimeLedger
    from saitenka.runtime.effects import Effect
    from saitenka.runtime.events import RuntimeEvent
    from saitenka.runtime.state import SessionReducer, SessionState

#: Which owner an event belongs to. None means the event is outside the routed vocabulary.
type OwnerOf = Callable[[RuntimeEvent], Owner | None]


class OwnerRouter:
    """Route declared events and count every event outside the declared graph."""

    def __init__(
        self,
        reducer: SessionReducer,
        owner_of: OwnerOf,
        *,
        ledger: RuntimeLedger | None = None,
        broadcast: tuple[type, ...] = (),
        passthrough: tuple[type, ...] = (),
    ) -> None:
        self._reducer = reducer
        self._owner_of = owner_of
        self._broadcast = broadcast
        self._passthrough = passthrough
        self._unrouted: Counter[str] = Counter()
        self._ledger = ledger

    @property
    def unrouted(self) -> dict[str, int]:
        """Unrouted events by `owner:EventType`."""
        return dict(self._unrouted)

    def _record_unrouted(self, key: str) -> None:
        self._unrouted[key] += 1
        if self._ledger is not None:
            self._ledger.unrouted(key)

    def _fan_out(
        self, state: SessionState, event: RuntimeEvent
    ) -> tuple[SessionState, tuple[Effect, ...]]:
        """Reduce a lifetime event into every slice that registers it, in one turn.

        A missing route is not counted here: a lifetime event is offered to every owner, and an
        owner with no facts for that lifetime legitimately has no route.
        """
        effects: tuple[Effect, ...] = ()
        for owner in Owner:
            try:
                result = self._reducer.reduce_turn(state, RoutedEvent(owner, event))
            except RouteError:
                continue
            state, effects = result.state, (*effects, *result.effects)
        return state, effects

    def __call__(
        self, state: SessionState, event: RuntimeEvent
    ) -> tuple[SessionState, tuple[Effect, ...]]:
        if isinstance(event, self._broadcast):
            return self._fan_out(state, event)
        if isinstance(event, self._passthrough):
            return state, ()
        owner = self._owner_of(event)
        if owner is None:
            self._record_unrouted(f"-:{type(event).__name__}")
            return state, ()
        try:
            result = self._reducer.reduce_turn(state, RoutedEvent(owner, event))
        except RouteError:
            self._record_unrouted(f"{owner.value}:{type(event).__name__}")
            return state, ()
        return result.state, result.effects
