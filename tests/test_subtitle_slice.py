"""`Owner.SUBTITLE`'s reducer and store.

The store test is the point of the file: a SessionController with a session reactor and one without must end
on the same selection, because only one of the two paths is the one production takes and the other
is the one almost every test takes.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.session_routes import install_session_reactor
from saitenka.runtime.events import (
    SubtitleLanguageChanged,
    SubtitlePrimaryAdopted,
    SubtitleSecondaryLeased,
    SubtitleStartupConfigured,
    SubtitleTrackAnnounced,
    SubtitleTracksDiscovered,
)
from saitenka.runtime.subtitle import SubtitleTrackState
from saitenka.runtime.subtitle_slice import SubtitleReducer, SubtitleTrackStore

#: One stream reaching every branch: a startup selection, a rescan, both role adoptions, a bare
#: language flip, a lease and its release, and an announcement.
STREAM = (
    SubtitleStartupConfigured(1, 2, "jp", "ja,jpn,jp"),
    SubtitleTracksDiscovered(3, 4),
    SubtitleSecondaryLeased(4),
    SubtitleTrackAnnounced(3),
    SubtitlePrimaryAdopted(4, "jp"),
    SubtitleLanguageChanged("en"),
    SubtitleSecondaryLeased(None),
)


def _fold(events) -> SubtitleTrackState:
    reducer = SubtitleReducer()
    state = SubtitleTrackState()
    for event in events:
        state = reducer.reduce(state, event)
    return state


def test_adopting_a_track_takes_it_off_the_role_it_already_held():
    # The override key ("what is on screen IS Japanese") can be pointed at the track already filed
    # as the translation. Leaving it in both would lease the reveal the very track it reveals.
    state = _fold((SubtitleStartupConfigured(1, 2, "jp", "ja"), SubtitlePrimaryAdopted(2, "jp")))

    assert (state.jp_sid, state.en_sid) == (2, None)


def test_adopting_a_new_track_leaves_the_other_role_alone():
    state = _fold((SubtitleStartupConfigured(1, 2, "jp", "ja"), SubtitlePrimaryAdopted(7, "en")))

    assert (state.jp_sid, state.en_sid, state.language) == (1, 7, "en")


def test_configuring_the_startup_selection_resets_every_carried_over_fact():
    """The reset is what keeps a session-lived slot episode-safe — see the re-slot test."""
    dirty = _fold((SubtitleTracksDiscovered(9, 8), SubtitleSecondaryLeased(8)))
    dirty = SubtitleReducer().reduce(dirty, SubtitleTrackAnnounced(9))

    fresh = SubtitleReducer().reduce(dirty, SubtitleStartupConfigured(None, None, "jp", "en,eng"))

    assert fresh == SubtitleTrackState(None, None, "jp", "en,eng")


def test_the_translation_track_is_the_role_the_primary_is_not():
    state = _fold((SubtitleStartupConfigured(1, 2, "jp", "ja"),))
    assert (state.primary_sid, state.translation_sid) == (1, 2)

    flipped = SubtitleReducer().reduce(state, SubtitleLanguageChanged("en"))
    assert (flipped.primary_sid, flipped.translation_sid) == (2, 1)


def test_the_reducer_refuses_an_event_from_another_owner():
    with pytest.raises(AssertionError):
        SubtitleReducer()(SubtitleTrackState(), object())  # type: ignore[arg-type]  # the point


def test_a_store_without_a_reactor_keeps_the_slice_itself():
    store = SubtitleTrackStore(FakeIPC())

    assert store.dispatch(SubtitleTracksDiscovered(1, 2)).jp_sid == 1
    assert store.current.en_sid == 2


def test_the_routed_store_and_the_local_one_end_on_the_same_selection(request):
    """The differential: whether the reactor or the store holds it must not change the answer."""
    local = SubtitleTrackStore(FakeIPC())
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = SubtitleTrackStore(ipc)

    for event in STREAM:
        local.dispatch(event)
        routed.dispatch(event)

    assert routed.current == local.current == _fold(STREAM)


def test_the_reactor_owned_slice_refuses_a_direct_write(request):
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = SubtitleTrackStore(ipc)

    with pytest.raises(RuntimeError):
        store.current = SubtitleTrackState(1, 2)
