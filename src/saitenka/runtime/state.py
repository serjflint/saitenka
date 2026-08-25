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
    handled: bool = True


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
class OwnerSlice:
    """The state of every feature sharing one owner slot, keyed by feature name.

    An owner slot is a single object, so without this the second feature to join an owner forces a
    rewrite of the first one's reducer — it would have to learn about a state it does not own.
    A tuple of pairs rather than a mapping: ordered, immutable, and an owner holds a handful of
    features, never enough for lookup cost to matter.
    """

    features: tuple[tuple[str, object], ...] = ()

    def get(self, key: str) -> object:
        for name, value in self.features:
            if name == key:
                return value
        raise KeyError(key)

    def replacing(self, key: str, value: object) -> OwnerSlice:
        return OwnerSlice(
            tuple((name, value if name == key else held) for name, held in self.features)
        )


class SliceReducer:
    """Dispatch one owner slot's event to every feature in it, threading the slot's state.

    Broadcast rather than lookup, because neither of the two dispatch keys available is sound.
    By event type: several features legitimately react to the same fact (a reconnect). By effect
    ownership: `EffectFinished` carries an `Owner`, not a feature — the issuer recognises its own
    by `identity` and every other feature already ignores what it does not recognise.
    """

    def __init__(self, features: dict[str, FeatureReducer]) -> None:
        self._features = dict(features)

    def initial(self, states: dict[str, object]) -> OwnerSlice:
        """The slot's starting state, ordered to match the reducers so dispatch order is fixed."""
        if states.keys() != self._features.keys():
            raise ValueError("every feature in the slice needs exactly one initial state")
        return OwnerSlice(tuple((key, states[key]) for key in self._features))

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, OwnerSlice)
        internal: list[RoutedEvent] = []
        effects: list[Effect] = []
        handled = False
        for key, reducer in self._features.items():
            result = reducer(state.get(key), event)
            state = state.replacing(key, result.state)
            internal.extend(result.internal_events)
            effects.extend(result.effects)
            handled = handled or result.handled
        return ReduceResult(state, tuple(internal), tuple(effects), handled)


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
            if not result.handled:
                raise RouteError(
                    f"no feature handled {current.owner.value}:{type(current.payload).__name__}"
                )
            next_state = next_state.with_owner(current.owner, result.state)
            pending.extend(result.internal_events)
            effects.extend(result.effects)
            if len(effects) > self._max_effects:
                raise RuntimeError("runtime effect limit exceeded")
        return TurnResult(next_state, tuple(effects))
