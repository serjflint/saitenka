"""`Owner.SUBTITLE`'s feature: one slice of track-selection state and its store.

Same shape as `playback_slice`, and deliberately so — one reducer, two stores, the choice made
once when the owner is built. What it does *not* have is an outbox: every SUBTITLE event is a
declaration, so there are no deltas to hand back to a sender already performing the action.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.events import (
    SUBTITLE_EVENTS,
    EpisodeRetired,
    EventEnvelope,
    EventOrigin,
    SubtitleLanguageChanged,
    SubtitlePrimaryAdopted,
    SubtitleSecondaryLeased,
    SubtitleStartupConfigured,
    SubtitleTrackAnnounced,
    SubtitleTracksDiscovered,
    SubtitleTranslationConfigured,
)
from saitenka.runtime.state import OwnerSlice, ReduceResult, SliceReducer
from saitenka.runtime.subtitle import SubtitleTrackState, adopt

if TYPE_CHECKING:
    from saitenka.runtime.events import RuntimeEvent, SubtitleEvent

#: What this slice reduces: its owner's vocabulary plus the one event that is nobody's.
type SubtitleSliceEvent = SubtitleEvent | EpisodeRetired


class SubtitleReducer:
    """Reduce one subtitle declaration. Pure: no mpv, no I/O, no clock."""

    def reduce(self, state: SubtitleTrackState, event: SubtitleSliceEvent) -> SubtitleTrackState:
        match event:
            case SubtitleStartupConfigured(
                jp_sid=jp,
                en_sid=en,
                language=language,
                slang=slang,
                second_slang=second_slang,
            ):
                # A whole-state reset, and that is what keeps this session-lived slot episode-safe:
                # a re-slot always runs `configure`, so nothing survives into the next file. It
                # supersedes the lease for a second reason too — `configure` also runs mid-session
                # on a live profile cycle, where a carried-over secondary leaves the reveal stuck
                # off, because `setup_secondary`'s `mirror == sid` guard skips re-issuing it.
                return SubtitleTrackState(jp, en, language, slang, second_slang=second_slang)
            case SubtitleTracksDiscovered(jp_sid=jp, en_sid=en):
                return replace(state, jp_sid=jp, en_sid=en)
            case SubtitleTranslationConfigured(
                primary_sid=primary, en_sid=en, second_slang=second_slang
            ):
                return replace(
                    state,
                    jp_sid=primary,
                    en_sid=en,
                    language="jp",
                    second_slang=second_slang,
                )
            case SubtitlePrimaryAdopted(sid=sid, language=language):
                return replace(adopt(state, sid, language), language=language)
            case SubtitleLanguageChanged(language=language):
                return replace(state, language=language)
            case SubtitleSecondaryLeased(sid=sid):
                return replace(state, secondary_sid=sid)
            case SubtitleTrackAnnounced(sid=sid):
                return replace(state, announced_sid=sid)
            # The named reset the slot's episode-safety rests on. A session-lived slot trades
            # Navigation-state rebinding is structural; this session-lived slice needs an explicit reset.
            case EpisodeRetired():
                return SubtitleTrackState()

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, SubtitleTrackState)
        assert isinstance(event, (*SUBTITLE_EVENTS, EpisodeRetired))
        return ReduceResult(self.reduce(state, event))


#: `Owner.SUBTITLE`'s first feature. Named once, like `PLAYBACK_FEATURE`, so the registration and
#: the reader of the slot cannot spell it differently.
SUBTITLE_FEATURE = "subtitle-tracks"


def subtitle_slice_reducer() -> SliceReducer:
    return SliceReducer({SUBTITLE_FEATURE: SubtitleReducer()})


def slice_of(slot: object) -> SubtitleTrackState:
    assert isinstance(slot, OwnerSlice)
    state = slot.get(SUBTITLE_FEATURE)
    assert isinstance(state, SubtitleTrackState)
    return state


class SubtitleRoutePort(Protocol):
    """Route one envelope to the session's reactor and hand back `SessionState.subtitle`.

    A `None` envelope reads the slot without routing, so "is there a reactor" and "what does it
    hold" stay one question — and a stand-in refuses by answering `None` rather than by lacking
    the method, which no `getattr` probe can tell from a rename.
    """

    def route_session_subtitle(self, envelope: object | None) -> object | None: ...


class SubtitleTrackStore:
    """Where `Owner.SUBTITLE`'s slice is kept — the reactor's slot, or here when there is none."""

    def __init__(self, port: SubtitleRoutePort, *, reducer: SubtitleReducer | None = None) -> None:
        self._reducer = reducer if reducer is not None else SubtitleReducer()
        self._state = SubtitleTrackState()
        self._port: SubtitleRoutePort | None = (
            port if port.route_session_subtitle(None) is not None else None
        )

    @property
    def routed(self) -> bool:
        """Whether the reactor owns this slice. Asked once, when an episode retires: a routed
        session fans one event out to every slice, an unrouted one has to reduce each store."""
        return self._port is not None

    @property
    def current(self) -> SubtitleTrackState:
        if self._port is None:
            return self._state
        return slice_of(self._port.route_session_subtitle(None))

    @current.setter
    def current(self, value: SubtitleTrackState) -> None:
        if self._port is not None:
            raise RuntimeError("the reactor owns this slice; send it an event")
        self._state = value

    def dispatch(self, event: SubtitleSliceEvent) -> SubtitleTrackState:
        """Reduce one declaration and return the state to read from afterwards."""
        if self._port is None:
            self._state = self._reducer.reduce(self._state, event)
            return self._state
        return slice_of(self._port.route_session_subtitle(_envelope(event)))


def _envelope(event: SubtitleSliceEvent) -> EventEnvelope:
    # These never enter the mailbox, so `sequence` is unread; the epoch is None because a track
    # selection is not epoch-fenced — a reconnect re-selects rather than replaying.
    return EventEnvelope(0, time.monotonic(), EventOrigin.MPV, None, event)
