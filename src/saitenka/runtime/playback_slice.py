"""`Owner.PLAYBACK`'s feature: one slice of state and the reducer that advances it.

The reduction lives here rather than at the call site so there is one of it. A Reader with no
session runtime installed drives this reducer directly; a Reader with one drives it through the
reactor's route table. Only the *store* differs — an inline second copy of the reduction would be
the untested path that drifts, which is the argument `LocalJobLane` already makes for job lanes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.events import (
    PLAYBACK_EVENTS,
    CueIdentityInstalled,
    CueIdentityRetireRequested,
    CueTextReplaced,
    EventEnvelope,
    EventOrigin,
    PropertyObserved,
    PropertySeeded,
    SourceReplaced,
)
from saitenka.runtime.playback import PlaybackProjection, PlaybackState
from saitenka.runtime.state import OwnerSlice, ReduceResult, SliceReducer

if TYPE_CHECKING:
    from saitenka.runtime.events import PlaybackEvent, RuntimeEvent
    from saitenka.runtime.playback import PlaybackDelta


@dataclass(frozen=True, slots=True)
class PlaybackSlice:
    """The projection plus what the turn that produced this slice published.

    `published` is an outbox, not a fact: it holds exactly the deltas of the event just reduced,
    and the next event replaces it. It sits on the state because `ReduceResult` has nowhere else
    for an output that is neither an effect nor an event a registered owner reduces — the
    consumers are still the Reader's `_apply_playback_delta` branches. When SUBTITLE, INTERACTION
    and PRESENTATION have slices, these become the turn's internal events and this field goes.
    """

    state: PlaybackState = field(default_factory=PlaybackState)
    published: tuple[PlaybackDelta, ...] = ()


class PlaybackReducer:
    """Reduce one playback event. Pure: the projection performs no I/O and holds no state."""

    def __init__(self, projection: PlaybackProjection | None = None) -> None:
        self._projection = projection if projection is not None else PlaybackProjection()

    @property
    def projection(self) -> PlaybackProjection:
        return self._projection

    def reduce(self, slice_: PlaybackSlice, event: PlaybackEvent) -> PlaybackSlice:
        projection = self._projection
        state = slice_.state
        match event:
            case PropertyObserved(name=name, data=data, connection_epoch=epoch):
                projected = projection.observe(state, name, data, connection_epoch=epoch)
                return PlaybackSlice(projected.state, projected.deltas)
            case PropertySeeded(name=name, data=data):
                return PlaybackSlice(projection.seed(state, name, data))
            case CueIdentityInstalled(start=start, end=end):
                return PlaybackSlice(projection.install(state, start=start, end=end))
            # The three declarations publish nothing, and that is the vocabulary's point rather
            # than a gap: the sender is the one retiring the identity or replacing the source, so
            # handing it `CueIdentityRetired` back would re-enter the teardown it is already in.
            # They become publishing events when the owner that acts on them is a slice.
            case CueIdentityRetireRequested(reason=reason):
                return PlaybackSlice(projection.retire(state, reason)[0])
            case CueTextReplaced(text=text):
                return PlaybackSlice(projection.cue_replaced(state, text))
            case SourceReplaced(path=path):
                return PlaybackSlice(projection.source_replaced(state, path).state)

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, PlaybackSlice)
        assert isinstance(event, PLAYBACK_EVENTS)
        return ReduceResult(self.reduce(state, event))


#: `Owner.PLAYBACK`'s only feature so far. Named once so the owner that registers it and the
#: reader of the slot cannot spell it differently, exactly as `Owner.SESSION`'s keys are.
PLAYBACK_FEATURE = "playback"


def playback_slice_reducer() -> SliceReducer:
    """The slot's reducer. A slice from the start, so a second playback feature is a registration."""
    return SliceReducer({PLAYBACK_FEATURE: PlaybackReducer()})


def slice_of(slot: object) -> PlaybackSlice:
    """Read `Owner.PLAYBACK`'s feature state out of the slot the reactor holds."""
    assert isinstance(slot, OwnerSlice)
    state = slot.get(PLAYBACK_FEATURE)
    assert isinstance(state, PlaybackSlice)
    return state


class PlaybackRoutePort(Protocol):
    """Route one envelope to the session's reactor and hand back `SessionState.playback`.

    A `None` envelope reads the slot without routing anything, so "is there a reactor" and "what
    does it hold" are one question — a transport that grew a second method to answer the same
    thing twice is a transport with two answers to keep consistent.

    Declared so a stand-in has to *refuse*: a `None` return means "no session reactor", which is a
    real answer every caller has a story for, and not one a `getattr` probe can tell from a rename.
    """

    def route_session_playback(self, envelope: object | None) -> object | None: ...


class PlaybackStore:
    """Where `Owner.PLAYBACK`'s slice is kept — the reactor's slot, or here when there is none.

    One reducer either way; only the store varies. The choice is made once, when the owner is
    built, because a store that switched mid-session would abandon every fact already observed
    into the one it left.
    """

    def __init__(self, port: PlaybackRoutePort, *, reducer: PlaybackReducer | None = None) -> None:
        self._reducer = reducer if reducer is not None else PlaybackReducer()
        self._slice = PlaybackSlice()
        self._port: PlaybackRoutePort | None = (
            port if port.route_session_playback(None) is not None else None
        )

    @property
    def current(self) -> PlaybackSlice:
        if self._port is None:
            return self._slice
        return slice_of(self._port.route_session_playback(None))

    @current.setter
    def current(self, value: PlaybackSlice) -> None:
        if self._port is not None:
            raise RuntimeError("the reactor owns this slice; send it an event")
        self._slice = value

    def dispatch(self, event: PlaybackEvent) -> tuple[PlaybackDelta, ...]:
        """Reduce one event and return the deltas to act on, empty when the turn did not run.

        Identity, not `published`: a reactor that is closing drops the event and leaves the
        previous turn's outbox in place, which a caller reading `published` blindly would apply a
        second time. Every reduction builds a new slice, so `is` answers exactly.
        """
        before = self.current
        if self._port is None:
            self._slice = self._reducer.reduce(before, event)
            return self._slice.published
        after = slice_of(self._port.route_session_playback(_envelope(event)))
        return () if after is before else after.published


def _envelope(event: PlaybackEvent) -> EventEnvelope:
    # `sequence` is the mailbox's ordering device and the reactor reads none of it; these events
    # never enter the mailbox. The epoch is None because the projection applies its own.
    return EventEnvelope(0, time.monotonic(), EventOrigin.MPV, None, event)
