"""The one event that belongs to no owner: an episode ending.

Subtitle navigation resets its facts by rebinding. An owner slot is
session-lived and has no such guarantee — so the reset is an event, and these are the assertions
that make the procedural guarantee as good as the structural one it replaced.
"""

from __future__ import annotations

from util import FakeIPC, bare_gateway

from saitenka.app import bindings as app_bindings
from saitenka.app.config import ReaderOptions
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.session.routes import install_session_reactor
from saitenka.runtime.events import (
    EpisodeRetired,
    HoverConfigured,
    HoverObserved,
    SubtitleStartupConfigured,
    TranslationDrawn,
    TranslationHeld,
)
from saitenka.runtime.hover import HoverDelays, HoverObservation
from saitenka.runtime.interaction_slice import HoverStore
from saitenka.runtime.presentation import TranslationState
from saitenka.runtime.presentation_slice import TranslationStore
from saitenka.runtime.subtitle import SubtitleTrackState
from saitenka.runtime.subtitle_slice import SubtitleTrackStore


def test_the_track_selection_does_not_survive_into_the_next_episode() -> None:
    """The slot's episode-safety used to rest on `configure` always running after a re-slot — true,
    and true only while that rule holds. This is the rule said out loud."""
    store = SubtitleTrackStore(FakeIPC())
    store.dispatch(SubtitleStartupConfigured(1, 2, "jp", "ja,jpn,jp"))

    assert store.dispatch(EpisodeRetired()) == SubtitleTrackState()


def test_no_dwell_armed_against_the_old_episode_can_fire_into_the_new_one() -> None:
    store = HoverStore(FakeIPC())
    store.dispatch(HoverConfigured(HoverDelays(scan=0.1, hide=0.2, switch=0.3)))
    store.dispatch(HoverObserved(HoverObservation(hover=0, word=1)))
    assert store.current.hysteresis.word_target == 1  # negative control

    store.dispatch(EpisodeRetired())

    assert store.current.hysteresis.word_target is None


def test_the_dwell_lengths_survive_because_they_are_the_sessions_not_the_episodes() -> None:
    delays = HoverDelays(scan=0.1, hide=0.2, switch=0.3)
    store = HoverStore(FakeIPC())
    store.dispatch(HoverConfigured(delays))

    store.dispatch(EpisodeRetired())

    assert store.current.delays == delays


def test_the_drawn_translation_goes_but_the_manual_hold_stays() -> None:
    """The hold is the user's standing preference; the drawn line belonged to the old cue."""
    store = TranslationStore(FakeIPC())
    store.dispatch(TranslationHeld(held=True))
    store.dispatch(TranslationDrawn("a line"))

    assert store.dispatch(EpisodeRetired()) == TranslationState(held=True, drawn=None)


def test_one_event_reaches_every_slice_when_a_reactor_owns_them(request) -> None:
    """One routed retirement turn reaches every owner.

    Routed through one store on purpose — the reactor fans out, so the caller must not have to
    send it four times and hope each reducer is idempotent.
    """
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    subtitle, hover = SubtitleTrackStore(ipc), HoverStore(ipc)
    translation = TranslationStore(ipc)
    subtitle.dispatch(SubtitleStartupConfigured(1, 2, "jp", "ja,jpn,jp"))
    hover.dispatch(HoverConfigured(HoverDelays(scan=0.1, hide=0.2, switch=0.3)))
    hover.dispatch(HoverObserved(HoverObservation(hover=0, word=1)))
    translation.dispatch(TranslationDrawn("a line"))

    subtitle.dispatch(EpisodeRetired())  # ONE store, not four

    assert subtitle.current == SubtitleTrackState()
    assert hover.current.hysteresis.word_target is None
    assert translation.current.drawn is None


def test_an_owner_with_no_per_episode_facts_is_not_counted_as_an_unrouted_gap(request) -> None:
    """`Owner.SESSION` registers no route for this event, and that is an answer.

    Broadcast fan-out offers the event to every owner; lacking per-episode facts is not an unrouted
    event and must not pollute the routing census.
    """
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    router = gateway.session_reactor._reducer  # the OwnerRouter the reactor was built with

    SubtitleTrackStore(ipc).dispatch(EpisodeRetired())

    assert not [key for key in router.unrouted if key.endswith("EpisodeRetired")]


def test_rebinding_the_episode_retires_the_slots_with_the_container(
    request, monkeypatch, make_session
) -> None:
    """Both halves move together or the slots keep the last episode's facts — silently, because
    nothing at the seam reads them until the next cue arrives."""

    ipc = FakeIPC()
    ipc.props["secondary-sub-text"] = "old line"
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    reader = make_session(ipc, options=ReaderOptions().with_overrides(prefetch=False))
    request.addfinalizer(reader.close)
    removed: list[int] = []
    monkeypatch.setattr(reader.graph.lifecycle_surfaces, "remove", removed.append)
    reader.command(app_bindings.TRANS_MSG)
    reader.graph.subtitle_tracks.dispatch(SubtitleStartupConfigured(1, 2, "jp", "ja,jpn,jp"))
    removed.clear()

    reader.graph.reslot.rebind_episode()

    assert reader.graph.subtitle_tracks.current == SubtitleTrackState()
    assert reader.graph.translation.state.held  # the hold is session-lived
    assert removed == [OverlayId.TRANS]
