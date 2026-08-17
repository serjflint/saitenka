"""Immutable reducer composition and closed event routing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from saitenka.runtime.effects import Effect, Owner
    from saitenka.runtime.events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class RoutedEvent:
    owner: Owner
    payload: RuntimeEvent


@dataclass(frozen=True, slots=True)
class ReduceResult:
    state: object
    internal_events: tuple[RoutedEvent, ...] = ()
    effects: tuple[Effect, ...] = ()


class FeatureReducer(Protocol):
    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult: ...


@dataclass(frozen=True, slots=True)
class SessionState:
    session: object
    playback: object
    subtitle: object
    interaction: object
    presentation: object

    def for_owner(self, owner: Owner) -> object:
        return getattr(self, owner.value)

    def with_owner(self, owner: Owner, value: object) -> SessionState:
        return replace(self, **{owner.value: value})


@dataclass(frozen=True, slots=True)
class RouteKey:
    event_type: type[object]
    owner: Owner


@dataclass(frozen=True, slots=True)
class TurnResult:
    state: SessionState
    effects: tuple[Effect, ...]


class RouteError(ValueError):
    pass


class SessionReducer:
    """Run one external event and its internal events as an atomic FIFO turn."""

    def __init__(
        self,
        routes: dict[RouteKey, FeatureReducer],
        *,
        max_internal_events: int = 256,
        max_effects: int = 256,
    ) -> None:
        if min(max_internal_events, max_effects) <= 0:
            raise ValueError("turn limits must be positive")
        self._routes = dict(routes)
        self._max_internal_events = max_internal_events
        self._max_effects = max_effects

    @property
    def route_keys(self) -> frozenset[RouteKey]:
        return frozenset(self._routes)

    def reduce_turn(
        self,
        state: SessionState,
        event: RoutedEvent,
    ) -> TurnResult:
        pending = [event]
        next_state = state
        effects: list[Effect] = []
        handled = 0
        while pending:
            current = pending.pop(0)
            handled += 1
            if handled > self._max_internal_events:
                raise RuntimeError("runtime internal-event limit exceeded")
            key = RouteKey(type(current.payload), current.owner)
            reducer = self._routes.get(key)
            if reducer is None:
                raise RouteError(
                    f"no reducer for {current.owner.value}:{type(current.payload).__name__}"
                )
            result = reducer(next_state.for_owner(current.owner), current.payload)
            next_state = next_state.with_owner(current.owner, result.state)
            pending.extend(result.internal_events)
            effects.extend(result.effects)
            if len(effects) > self._max_effects:
                raise RuntimeError("runtime effect limit exceeded")
        return TurnResult(next_state, tuple(effects))
