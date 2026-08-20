"""`Owner.PLAYBACK`'s reducer against the projection it wraps.

The differential test is the point of the file: item 13 moves the projection's call sites behind a
reducer, and the only thing that makes that safe is that the same stream produces the same state
and the same deltas *in the same order*. A per-event unit test cannot see an ordering change.
"""

from __future__ import annotations

import pytest

from saitenka.runtime.events import (
    CueIdentityInstalled,
    CueIdentityRetireRequested,
    CueTextReplaced,
    PropertyObserved,
    PropertySeeded,
    SourceReplaced,
)
from saitenka.runtime.playback import PlaybackProjection, PlaybackState, RetireReason
from saitenka.runtime.playback_slice import PlaybackReducer, PlaybackSlice
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
    assert result.effects == ()


def test_the_reducer_refuses_an_event_that_is_not_playbacks() -> None:
    from saitenka.runtime.events import StartupReady

    with pytest.raises(AssertionError):
        PlaybackReducer()(PlaybackSlice(), StartupReady())
