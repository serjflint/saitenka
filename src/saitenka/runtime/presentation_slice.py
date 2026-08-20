"""`Owner.PRESENTATION`'s feature: the translation reveal and its store.

SUBTITLE's shape, not INTERACTION's: every event is a declaration of something the sender has
already drawn, so there is nothing to hand back and the slice needs no outbox.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.events import (
    PRESENTATION_EVENTS,
    EventEnvelope,
    EventOrigin,
    TranslationDrawn,
    TranslationHeld,
)
from saitenka.runtime.presentation import TranslationState
from saitenka.runtime.state import OwnerSlice, ReduceResult, SliceReducer

if TYPE_CHECKING:
    from saitenka.runtime.events import PresentationEvent, RuntimeEvent


class TranslationReducer:
    """Reduce one presentation declaration. Pure: no overlay, no mpv, no clock."""

    def reduce(self, state: TranslationState, event: PresentationEvent) -> TranslationState:
        match event:
            case TranslationHeld(held=held):
                return replace(state, held=held)
            case TranslationDrawn(text=text):
                return replace(state, drawn=text)

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, TranslationState)
        assert isinstance(event, PRESENTATION_EVENTS)
        return ReduceResult(self.reduce(state, event))


#: `Owner.PRESENTATION`'s first feature.
PRESENTATION_FEATURE = "translation"


def presentation_slice_reducer() -> SliceReducer:
    return SliceReducer({PRESENTATION_FEATURE: TranslationReducer()})


def slice_of(slot: object) -> TranslationState:
    assert isinstance(slot, OwnerSlice)
    state = slot.get(PRESENTATION_FEATURE)
    assert isinstance(state, TranslationState)
    return state


class PresentationRoutePort(Protocol):
    """Route one envelope to the session's reactor and hand back `SessionState.presentation`.

    A `None` envelope reads the slot without routing, so "is there a reactor" and "what does it
    hold" stay one question — and a stand-in refuses by answering `None` rather than by lacking the
    method, which no `getattr` probe can tell from a rename.
    """

    def route_session_presentation(self, envelope: object | None) -> object | None: ...


class TranslationStore:
    """Where `Owner.PRESENTATION`'s slice is kept — the reactor's slot, or here when there is none."""

    def __init__(
        self, port: PresentationRoutePort, *, reducer: TranslationReducer | None = None
    ) -> None:
        self._reducer = reducer if reducer is not None else TranslationReducer()
        self._state = TranslationState()
        self._port: PresentationRoutePort | None = (
            port if port.route_session_presentation(None) is not None else None
        )

    @property
    def current(self) -> TranslationState:
        if self._port is None:
            return self._state
        return slice_of(self._port.route_session_presentation(None))

    @current.setter
    def current(self, value: TranslationState) -> None:
        if self._port is not None:
            raise RuntimeError("the reactor owns this slice; send it an event")
        self._state = value

    def dispatch(self, event: PresentationEvent) -> TranslationState:
        """Reduce one declaration and return the state to read from afterwards."""
        if self._port is None:
            self._state = self._reducer.reduce(self._state, event)
            return self._state
        return slice_of(self._port.route_session_presentation(_envelope(event)))


def _envelope(event: PresentationEvent) -> EventEnvelope:
    # These never enter the mailbox, so `sequence` is unread; the epoch is None because a reveal is
    # re-decided on reconnect rather than replayed.
    return EventEnvelope(0, time.monotonic(), EventOrigin.PRESENTATION, None, event)
