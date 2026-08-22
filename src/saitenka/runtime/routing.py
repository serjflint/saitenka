"""Adapt the owner-routed reducer to the reactor, and make "not migrated yet" a fact, not an error.

`SessionReactor` wants `(state, event) -> (state, effects)`; `SessionReducer.reduce_turn` wants
`(SessionState, RoutedEvent) -> TurnResult`. The two were written to fit and had never been composed
— every reactor test hand-rolls a local reducer instead.

The gap that matters is not the shape but the failure mode. `reduce_turn` raises `RouteError` for an
event with no registered route, which is correct once the migration is done and wrong during it:
while features move one `Owner` at a time, most events have no route *yet*. Worse, `RouteError`
subclasses `ValueError`, and `Reader.pump` catches `ValueError` and reports it as "mpv went away" —
so an unmigrated event would end the session silently.

So an unrouted event is ignored here, and **counted**. A route that was never registered otherwise
looks exactly like a feature that was never migrated, and the difference is the whole migration.
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

#: Which owner an event belongs to. Returns None while nothing owns it yet — the migration's
#: "not mine" answer, distinct from "mine and it failed".
type OwnerOf = Callable[[RuntimeEvent], Owner | None]


class OwnerRouter:
    """The reactor's reducer during the migration: routes what is owned, counts what is not."""

    def __init__(
        self,
        reducer: SessionReducer,
        owner_of: OwnerOf,
        *,
        ledger: RuntimeLedger | None = None,
        broadcast: tuple[type, ...] = (),
    ) -> None:
        self._reducer = reducer
        self._owner_of = owner_of
        self._broadcast = broadcast
        self._ignored: Counter[str] = Counter()
        self._ledger = ledger

    @property
    def ignored(self) -> dict[str, int]:
        """Unrouted events by `owner:EventType`, so the gap is observable rather than assumed."""
        return dict(self._ignored)

    def _ignore(self, key: str) -> None:
        self._ignored[key] += 1
        if self._ledger is not None:
            self._ledger.ignored(key)

    def _fan_out(
        self, state: SessionState, event: RuntimeEvent
    ) -> tuple[SessionState, tuple[Effect, ...]]:
        """Reduce a lifetime event into every slice that registers it, in one turn.

        A missing route is NOT counted here, unlike everywhere else in this class: for an owned
        event it means "not migrated yet", but for a lifetime event it means "this owner has no
        per-episode facts", which is a permanent and correct answer.
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
        owner = self._owner_of(event)
        if owner is None:
            self._ignore(f"-:{type(event).__name__}")
            return state, ()
        try:
            result = self._reducer.reduce_turn(state, RoutedEvent(owner, event))
        except RouteError:
            self._ignore(f"{owner.value}:{type(event).__name__}")
            return state, ()
        return result.state, result.effects
