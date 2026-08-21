"""`Owner.PLAYBACK`'s reducer against the projection it wraps.

The differential test is the point of the file: item 13 moves the projection's call sites behind a
reducer, and the only thing that makes that safe is that the same stream produces the same state
and the same deltas *in the same order*. A per-event unit test cannot see an ordering change.
"""

from __future__ import annotations

import pytest

from saitenka.runtime.effects import ApplyPlaybackDeltas
from saitenka.runtime.events import (
    CueIdentityInstalled,
    CueIdentityRetireRequested,
    CueTextReplaced,
    PropertyObserved,
    PropertySeeded,
    SourceReplaced,
)
from saitenka.runtime.playback import (
    PauseChanged,
    PlaybackProjection,
    PlaybackState,
    RetireReason,
)
from saitenka.runtime.playback_slice import PlaybackReducer, PlaybackSlice, slice_of
from saitenka.runtime.state import ReduceResult

#: One stream that reaches every branch: a seed, a track selection, a split cue burst, an
#: installed identity, the conflict that retires it, and both Reader-side declarations.
STREAM = (
    PropertySeeded("osd-dimensions", {"w": 1920, "h": 1080}),
    PropertyObserved("sid", 1),
    PropertyObserved("sub-start", 1.0),
    PropertyObserved("sub-text", "ある日"),
    PropertyObserved("sub-end", 3.0),
    CueIdentityInstalled(1.0, 3.0),
    PropertyObserved("sub-end", 4.0),
    PropertyObserved("time-pos", 2.0),
    PropertyObserved("sub-delay", 0.5),
    PropertyObserved("mouse-pos", {"x": 10, "y": 20}),
    PropertyObserved("pause", data=True),
    PropertyObserved("eof-reached", data=True),
    PropertyObserved("options/sub-scale", 1.2),
    CueTextReplaced(""),
    SourceReplaced("/tmp/next.srt"),
    CueIdentityRetireRequested(RetireReason.TRACK),
)


def _direct(projection: PlaybackProjection, state: PlaybackState, event: object) -> tuple:
    """The call shape `Reader` used before the reducer existed, verbatim per event kind."""
    match event:
        case PropertyObserved(name=name, data=data):
            projected = projection.observe(state, name, data)
            return projected.state, projected.deltas
        case PropertySeeded(name=name, data=data):
            return projection.seed(state, name, data), ()
        case CueIdentityInstalled(start=start, end=end):
            return projection.install(state, start=start, end=end), ()
        # The three declarations discarded their deltas at the call site; the reducer's
        # `published` has to stay empty for exactly the same reason.
        case CueIdentityRetireRequested(reason=reason):
            return projection.retire(state, reason)[0], ()
        case CueTextReplaced(text=text):
            return projection.cue_replaced(state, text), ()
        case SourceReplaced(path=path):
            return projection.source_replaced(state, path).state, ()
    raise AssertionError(event)


def test_reducer_matches_the_projection_state_and_delta_order() -> None:
    reducer = PlaybackReducer()
    projection = PlaybackProjection()
    slice_ = PlaybackSlice()
    direct = PlaybackState()
    for event in STREAM:
        slice_ = reducer.reduce(slice_, event)
        direct, deltas = _direct(projection, direct, event)
        assert slice_.state == direct
        assert slice_.published == deltas


def test_published_is_the_last_turns_outbox_not_an_accumulator() -> None:
    reducer = PlaybackReducer()
    slice_ = reducer.reduce(PlaybackSlice(), PropertyObserved("sub-text", "hello"))
    assert slice_.published
    assert reducer.reduce(slice_, PropertySeeded("sid", 1)).published == ()


def test_a_declaration_publishes_nothing_so_the_sender_is_not_told_its_own_news() -> None:
    reducer = PlaybackReducer()
    slice_ = reducer.reduce(PlaybackSlice(), CueIdentityInstalled(1.0, 2.0))
    retired = reducer.reduce(slice_, CueIdentityRetireRequested(RetireReason.SOURCE))
    assert retired.state.cue.installed is None
    assert retired.published == ()


def test_the_feature_call_shape_returns_a_reduce_result() -> None:
    reducer = PlaybackReducer()
    result = reducer(PlaybackSlice(), PropertyObserved("pause", data=True))
    assert isinstance(result, ReduceResult)
    assert isinstance(result.state, PlaybackSlice)
    assert result.state.state.paused is True
    assert result.internal_events == ()
    # The routed half's outbox: `reduce` publishes into the slice, `__call__` hands it out as the
    # effect a mailbox-delivered observation has no caller to return deltas to.
    assert result.effects == (ApplyPlaybackDeltas((PauseChanged(paused=True),)),)


def test_the_reducer_refuses_an_event_that_is_not_playbacks() -> None:
    from saitenka.runtime.events import StartupReady

    with pytest.raises(AssertionError):
        PlaybackReducer()(PlaybackSlice(), StartupReady())


def _reader_with_a_session_runtime(request):
    from util import FakeIPC, runtime_gateway

    from saitenka.app.controller import Reader
    from saitenka.app.session_routes import install_session_reactor
    from saitenka.app.subtitle_render import NullRenderer

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = Reader(ipc, renderer=NullRenderer())
    request.addfinalizer(reader.close)
    return reader, gateway


def test_a_session_runtime_owns_the_slot_the_reader_observes_into(request) -> None:
    """The whole point of item 13: with a runtime installed there is no Reader-side copy."""
    reader, gateway = _reader_with_a_session_runtime(request)
    reader._observe_property("sub-text", "こんにちは")

    slot = gateway.session_reactor.state.playback
    assert slice_of(slot).state.value("sub-text") == "こんにちは"
    assert reader._playback is slice_of(slot).state


def test_a_reader_with_no_runtime_still_observes_into_its_own_slice(request) -> None:
    from util import FakeIPC

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import NullRenderer

    reader = Reader(FakeIPC(), renderer=NullRenderer())
    request.addfinalizer(reader.close)
    reader._observe_property("sub-text", "ただいま")

    assert reader._playback.value("sub-text") == "ただいま"


def test_a_closed_reactor_drops_the_event_instead_of_replaying_the_last_outbox(request) -> None:
    """A dropped turn leaves `published` in place; applying it again would double every delta."""
    reader, gateway = _reader_with_a_session_runtime(request)
    reader._observe_property("sub-text", "one")
    gateway.session_reactor.close()

    store = reader._playback_store
    assert store.dispatch(PropertyObserved("sub-text", "two")) == ()


def test_an_empty_routed_dispatch_does_not_mean_the_event_did_nothing(request) -> None:
    """`dispatch` returns two different things under one type, and this pins which is which.

    Its seven peer stores hand back the routed slice's `published`; this one alone returns `()`,
    because a routed playback turn has already applied its deltas through `ApplyPlaybackDeltas` and
    returning them again would apply the turn twice. So `()` here means *handled elsewhere*, not
    *nothing happened*, and `routed` is the only thing that tells a caller which — an asymmetry
    worth a failing test the day someone unifies the four stores on their peers' shape.
    """
    reader, _gateway = _reader_with_a_session_runtime(request)
    store = reader._playback_store
    assert store.routed

    assert store.dispatch(PropertyObserved("sub-text", "ただいま")) == ()
    assert store.current.state.cue.text == "ただいま"
