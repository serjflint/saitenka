"""`Owner.INTERACTION`'s feature: the hover hysteresis, its dwell configuration, and its outbox.

The third slice, and the first whose events are *observations* rather than declarations. SUBTITLE
needs no outbox because the sender has already done the thing it is declaring; here the reducer is
what decides, so the turn's decisions have to come back. `published` is that outbox: the caller
drains it immediately after routing, so a decision is still performed synchronously, in order,
where it was.

`published` is read off the slice the turn produced, never off the store — a dropped turn (the
reactor ignores an event while closing) leaves the previous turn's outbox in place, and slice
identity is the only thing that answers whether this turn is the one that filled it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.events import (
    INTERACTION_EVENTS,
    EventEnvelope,
    EventOrigin,
    HoverConfigured,
    HoverDwellElapsed,
    HoverDwellRefused,
    HoverObserved,
    HoverScrolled,
)
from saitenka.runtime.hover import HoverDelays, HoverState, decide, elapsed, refused, scrolled
from saitenka.runtime.state import OwnerSlice, ReduceResult, SliceReducer

if TYPE_CHECKING:
    from saitenka.runtime.events import InteractionEvent, RuntimeEvent
    from saitenka.runtime.hover import Decision

#: Until a session declares its own. Zero would make every dwell fire instantly, which is the one
#: default that changes behaviour rather than deferring it.
DEFAULT_DELAYS = HoverDelays(scan=0.25, hide=0.35, switch=0.25)


@dataclass(frozen=True, slots=True)
class HoverFeature:
    """The slice: the hysteresis, the configuration it is decided against, and the turn's outbox."""

    hysteresis: HoverState = field(default_factory=HoverState)
    delays: HoverDelays = DEFAULT_DELAYS
    published: tuple[Decision, ...] = ()


class HoverReducer:
    """Reduce one interaction observation. Pure: no host, no timers, no clock."""

    def reduce(self, state: HoverFeature, event: InteractionEvent) -> HoverFeature:
        match event:
            case HoverConfigured(delays=delays):
                return replace(state, delays=delays, published=())
            case HoverObserved(observation=observation):
                turn = decide(state.hysteresis, observation, state.delays)
            case HoverDwellElapsed(intent=intent, nest_tail=tail):
                turn = elapsed(state.hysteresis, intent, nest_tail=tail)
            case HoverScrolled(nested=nested):
                turn = scrolled(state.hysteresis, nested=nested)
            case HoverDwellRefused(intent=intent):
                turn = refused(state.hysteresis, intent)
        return replace(state, hysteresis=turn.state, published=turn.decisions)

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, HoverFeature)
        assert isinstance(event, INTERACTION_EVENTS)
        return ReduceResult(self.reduce(state, event))


#: `Owner.INTERACTION`'s first feature.
INTERACTION_FEATURE = "hover"


def interaction_slice_reducer() -> SliceReducer:
    return SliceReducer({INTERACTION_FEATURE: HoverReducer()})


def slice_of(slot: object) -> HoverFeature:
    assert isinstance(slot, OwnerSlice)
    state = slot.get(INTERACTION_FEATURE)
    assert isinstance(state, HoverFeature)
    return state


class InteractionRoutePort(Protocol):
    """Route one envelope to the session's reactor and hand back `SessionState.interaction`.

    A `None` envelope reads the slot without routing, so "is there a reactor" and "what does it
    hold" stay one question — and a stand-in refuses by answering `None` rather than by lacking the
    method, which no `getattr` probe can tell from a rename.
    """

    def route_session_interaction(self, envelope: object | None) -> object | None: ...


class HoverStore:
    """Where `Owner.INTERACTION`'s slice is kept — the reactor's slot, or here when there is none.

    The choice is made once, when the owner is built: a store that could switch mid-session would
    abandon every dwell already armed against the state it left.
    """

    def __init__(self, port: InteractionRoutePort, *, reducer: HoverReducer | None = None) -> None:
        self._reducer = reducer if reducer is not None else HoverReducer()
        self._state = HoverFeature()
        self._port: InteractionRoutePort | None = (
            port if port.route_session_interaction(None) is not None else None
        )

    @property
    def current(self) -> HoverFeature:
        if self._port is None:
            return self._state
        return slice_of(self._port.route_session_interaction(None))

    @current.setter
    def current(self, value: HoverFeature) -> None:
        if self._port is not None:
            raise RuntimeError("the reactor owns this slice; send it an event")
        self._state = value

    def dispatch(self, event: InteractionEvent) -> tuple[Decision, ...]:
        """Reduce one observation and drain the turn's outbox."""
        if self._port is None:
            self._state = self._reducer.reduce(self._state, event)
            return self._state.published
        return slice_of(self._port.route_session_interaction(_envelope(event))).published


def _envelope(event: InteractionEvent) -> EventEnvelope:
    # These never enter the mailbox, so `sequence` is unread; the epoch is None because a hover is
    # not epoch-fenced — a reconnect re-observes rather than replaying.
    return EventEnvelope(0, time.monotonic(), EventOrigin.MPV, None, event)
