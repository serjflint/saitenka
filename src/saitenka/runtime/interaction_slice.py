"""`Owner.INTERACTION`'s features: the hover hysteresis, and the shortcut overlay.

The third slice, and the first whose events are *observations* rather than declarations. SUBTITLE
needs no outbox because the sender has already done the thing it is declaring; here the reducer is
what decides, so the turn's decisions have to come back. `published` is that outbox: the caller
drains it immediately after routing, so a decision is still performed synchronously, in order,
where it was.

`published` is read off the slice the turn produced, never off the store — a dropped turn (the
reactor ignores an event while closing) leaves the previous turn's outbox in place, and slice
identity is the only thing that answers whether this turn is the one that filled it.

Two features share the slot, which is what `OwnerSlice` exists for: `help` joined by registering a
reducer and an initial state, with nothing in `hover` changing. Dispatch inside the slice is a
broadcast, so each feature clears its own outbox on an event it does not own — a stale outbox reads
to its caller as a decision this turn made.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.events import (
    INTERACTION_EVENTS,
    EpisodeRetired,
    EventEnvelope,
    EventOrigin,
    HelpCommanded,
    HelpRepaginated,
    HoverConfigured,
    HoverDwellElapsed,
    HoverDwellRefused,
    HoverObserved,
    HoverScrolled,
)
from saitenka.runtime.help import HelpCommand, HelpState, repaginate
from saitenka.runtime.help import decide as decide_help
from saitenka.runtime.hover import HoverDelays, HoverState, decide, elapsed, refused, scrolled
from saitenka.runtime.state import OwnerSlice, ReduceResult, SliceReducer

if TYPE_CHECKING:
    from saitenka.runtime.events import InteractionEvent, RuntimeEvent
    from saitenka.runtime.help import HelpEffect
    from saitenka.runtime.hover import Decision

#: What this slice reduces: its owner's vocabulary plus the one event that is nobody's.
type InteractionSliceEvent = InteractionEvent | EpisodeRetired

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

    def reduce(self, state: HoverFeature, event: InteractionSliceEvent) -> HoverFeature:
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
            # Nothing is hovered in a new episode, and no dwell armed against the old one may fire
            # into it. The delays survive: they are the session's configuration, not the episode's.
            case EpisodeRetired():
                return replace(state, hysteresis=HoverState(), published=())
            case _:
                # The slot's other feature's events, arriving by broadcast. Nothing to decide, and
                # the outbox clears for the same reason it does over there.
                return replace(state, published=())
        return replace(state, hysteresis=turn.state, published=turn.decisions)

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, HoverFeature)
        assert isinstance(event, (*INTERACTION_EVENTS, EpisodeRetired))
        return ReduceResult(self.reduce(state, event))


@dataclass(frozen=True, slots=True)
class HelpFeature:
    """The slice: what the shortcut overlay shows, and the turn's outbox."""

    overlay: HelpState = field(default_factory=HelpState)
    published: tuple[HelpEffect, ...] = ()


class HelpReducer:
    """Reduce one shortcut-overlay command. Pure: no document, no screen, no host."""

    def reduce(self, state: HelpFeature, event: RuntimeEvent) -> HelpFeature:
        match event:
            case HelpCommanded(command=command, page_count=page_count):
                turn = decide_help(state.overlay, command, page_count=page_count)
            case HelpRepaginated(page_count=page_count):
                turn = repaginate(state.overlay, page_count)
            case _:
                # Every other interaction event reaches here by broadcast and decides nothing. The
                # outbox still clears: leaving the last command's decisions in place would replay
                # them on whatever drains next.
                return replace(state, published=())
        return HelpFeature(turn.state, turn.decisions)

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, HelpFeature)
        return ReduceResult(self.reduce(state, event))


#: `Owner.INTERACTION`'s features, named once so a reader of the slot never spells a key itself.
INTERACTION_FEATURE = "hover"
HELP_FEATURE = "help"


def interaction_slice_reducer() -> SliceReducer:
    return SliceReducer({INTERACTION_FEATURE: HoverReducer(), HELP_FEATURE: HelpReducer()})


def slice_of(slot: object) -> HoverFeature:
    assert isinstance(slot, OwnerSlice)
    state = slot.get(INTERACTION_FEATURE)
    assert isinstance(state, HoverFeature)
    return state


def help_slice_of(slot: object) -> HelpFeature:
    assert isinstance(slot, OwnerSlice)
    state = slot.get(HELP_FEATURE)
    assert isinstance(state, HelpFeature)
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
    def routed(self) -> bool:
        """Whether the reactor owns this slice. Asked once, when an episode retires: a routed
        session fans one event out to every slice, an unrouted one has to reduce each store."""
        return self._port is not None

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

    def dispatch(self, event: InteractionSliceEvent) -> tuple[Decision, ...]:
        """Reduce one observation and drain the turn's outbox."""
        if self._port is None:
            self._state = self._reducer.reduce(self._state, event)
            return self._state.published
        return slice_of(self._port.route_session_interaction(_envelope(event))).published


class HelpStore:
    """Where the shortcut overlay's slice is kept, chosen once — same rule as `HoverStore`.

    A separate store over the *same* port rather than a second method on `HoverStore`: the two
    features are independent registrations in one slot, and a store that read both would have to be
    rewritten every time a third joins.
    """

    def __init__(self, port: InteractionRoutePort, *, reducer: HelpReducer | None = None) -> None:
        self._reducer = reducer if reducer is not None else HelpReducer()
        self._state = HelpFeature()
        self._port: InteractionRoutePort | None = (
            port if port.route_session_interaction(None) is not None else None
        )

    @property
    def current(self) -> HelpState:
        """What the overlay shows right now. Frozen, so a caller cannot write the fact it reads."""
        if self._port is None:
            return self._state.overlay
        return help_slice_of(self._port.route_session_interaction(None)).overlay

    def dispatch(self, command: HelpCommand, *, page_count: int = 0) -> tuple[HelpEffect, ...]:
        """Decide one command and drain the turn's outbox."""
        return self._reduce(HelpCommanded(command, page_count))

    def repaginate(self, page_count: int) -> None:
        """Fold a freshly measured document length in. Decides nothing, so there is nothing to drain."""
        self._reduce(HelpRepaginated(page_count))

    def _reduce(self, event: InteractionSliceEvent) -> tuple[HelpEffect, ...]:
        if self._port is None:
            self._state = self._reducer.reduce(self._state, event)
            return self._state.published
        return help_slice_of(self._port.route_session_interaction(_envelope(event))).published


def _envelope(event: InteractionSliceEvent) -> EventEnvelope:
    # These never enter the mailbox, so `sequence` is unread; the epoch is None because a hover is
    # not epoch-fenced — a reconnect re-observes rather than replaying.
    return EventEnvelope(0, time.monotonic(), EventOrigin.MPV, None, event)
