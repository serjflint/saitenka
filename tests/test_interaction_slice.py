"""`Owner.INTERACTION`'s reducers, their outboxes, and their stores.

The store test is the point of the file, as it is for subtitle: a Reader with a session reactor and
one without must decide the same hover, because only one of the two paths is the one production
takes and the other is the one almost every test takes.

The outbox is what is new here. SUBTITLE declares, so its turns hand back nothing; INTERACTION
observes, so the reducer is what decides and the decisions have to come back.

Two features share this slot, so the second thing the file pins is that they are independent: one
feature's events reach the other by broadcast, and each has to leave the other's state alone.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.session_routes import install_session_reactor
from saitenka.runtime.events import (
    EpisodeRetired,
    HelpCommanded,
    HoverConfigured,
    HoverDwellElapsed,
    HoverDwellRefused,
    HoverObserved,
    HoverScrolled,
)
from saitenka.runtime.help import HelpCommand, HelpState, OpenHelp, ShowHelpPage
from saitenka.runtime.hover import (
    Arm,
    Dwell,
    HoverDelays,
    HoverObservation,
    HoverState,
    RetireWord,
    ShowWord,
    SwitchTo,
)
from saitenka.runtime.interaction_slice import (
    HelpFeature,
    HelpReducer,
    HelpStore,
    HoverFeature,
    HoverReducer,
    HoverStore,
)

DELAYS = HoverDelays(scan=0.1, hide=0.2, switch=0.3)

#: One stream reaching every entry the reducer has: configuration, an observation that opens, an
#: observation that arms a switch dwell, that dwell firing, a scroll, and a leave.
STREAM = (
    HoverConfigured(DELAYS),
    HoverObserved(HoverObservation(hover=-1, word=0)),
    HoverObserved(HoverObservation(hover=0, word=1)),
    HoverDwellElapsed(SwitchTo(1)),
    HoverScrolled(nested=False),
    HoverObserved(HoverObservation(hover=1, word=-1)),
)


def _fold(events) -> list[tuple]:
    """Every turn's outbox, in order — what a caller draining the slice would have performed."""
    reducer = HoverReducer()
    state = HoverFeature()
    drained = []
    for event in events:
        state = reducer.reduce(state, event)
        drained.append(state.published)
    return drained


def test_the_configured_delays_are_what_the_next_decision_is_made_against() -> None:
    """Configuration is state the reducer reads in the turn, so it has to arrive as an event —
    reading it off the host is exactly the impurity the slice exists to remove."""
    reducer = HoverReducer()
    configured = reducer.reduce(HoverFeature(), HoverConfigured(DELAYS))

    turn = reducer.reduce(configured, HoverObserved(HoverObservation(hover=0, word=1)))

    assert Arm(Dwell.SWITCH, 0.3, SwitchTo(1)) in turn.published


def test_each_turn_publishes_its_own_decisions_and_no_earlier_ones() -> None:
    """A stale outbox is the failure the slice's identity guards against: applying the previous
    turn's decisions a second time re-enters a teardown that already ran."""
    drained = _fold(STREAM)

    assert drained[0] == ()  # configuration decides nothing
    assert ShowWord(0) in drained[1]  # first word opens instantly
    assert ShowWord(0) not in drained[2]  # …and is not published again


def test_a_dwell_that_elapses_publishes_the_switch_it_was_armed_for() -> None:
    drained = _fold(STREAM)

    assert drained[3] == (ShowWord(1),)


def test_leaving_the_word_publishes_a_linger_rather_than_a_teardown() -> None:
    drained = _fold(STREAM)

    assert not [d for d in drained[-1] if isinstance(d, RetireWord)]
    assert [d for d in drained[-1] if isinstance(d, Arm)]


def test_a_refusal_is_reduced_like_any_other_outcome() -> None:
    """The reducer owns the fail-open answer, so the caller does not have to know one exists."""
    reducer = HoverReducer()
    state = reducer.reduce(HoverFeature(), HoverConfigured(DELAYS))
    state = reducer.reduce(state, HoverObserved(HoverObservation(hover=0, word=1)))

    assert reducer.reduce(state, HoverDwellRefused(SwitchTo(1))).published == (ShowWord(1),)


def test_a_scroll_stops_the_linger_being_pending() -> None:
    reducer = HoverReducer()
    state = reducer.reduce(HoverFeature(), HoverConfigured(DELAYS))
    state = reducer.reduce(state, HoverObserved(HoverObservation(hover=0, word=-1)))
    assert state.hysteresis.tip_hide_pending  # negative control

    assert not reducer.reduce(state, HoverScrolled(nested=False)).hysteresis.tip_hide_pending


# --- the store: the same stream, either side of the reactor --------------------------------------


def test_the_same_stream_decides_the_same_hover_with_or_without_a_reactor(request) -> None:
    """The differential, and the reason the store exists in this shape. A session with a reactor
    keeps the slice in `SessionState.interaction`; one without keeps it here. Neither path may
    decide differently, or the migration changed behaviour for exactly the sessions nothing tests.
    """
    local = HoverStore(FakeIPC())

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = HoverStore(ipc)

    assert [local.dispatch(e) for e in STREAM] == [routed.dispatch(e) for e in STREAM]
    assert local.current.hysteresis == routed.current.hysteresis


def test_the_store_without_a_reactor_holds_the_slice_itself() -> None:
    store = HoverStore(FakeIPC())

    store.dispatch(HoverConfigured(DELAYS))

    assert store.current.delays == DELAYS


def test_a_reactor_owned_slice_refuses_a_write_that_bypasses_it(request) -> None:
    """A second writer of a slot the reactor owns is a state the runtime never saw — so it is
    refused rather than silently kept beside the real one."""
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = HoverStore(ipc)

    with pytest.raises(RuntimeError):
        store.current = HoverFeature(HoverState(word_target=3))


def test_the_hover_view_projects_what_the_slice_holds() -> None:
    """The four `TooltipState` fields are a projection now. Two copies that can disagree is the
    thing this pins: the dwell target reported to a caller is the one the machine armed."""
    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import NullRenderer

    ipc = FakeIPC()
    ipc.props["mouse-pos"] = {"hover": True, "x": 5, "y": 5}
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer(), hover_switch_delay=10.0)
    try:
        reader.tokens = [object(), object()]
        reader.hover = 0
        reader._hit = lambda *_args: 1  # type: ignore[method-assign]

        reader._update_hover()

        assert reader._hover_store.current.hysteresis.word_target == 1
        assert reader.tip.word_target == 1
        assert reader.hover_view().scan_target is None
    finally:
        reader.close()


# --- the slot's second feature --------------------------------------------------------------------


def test_help_and_hover_share_the_slot_without_reading_each_other(request) -> None:
    """`help` joined by registering a reducer and an initial state — nothing in `hover` moved.

    The proof is the broadcast: `SliceReducer` hands every event to every feature, so a hover
    stream reaches `HelpReducer` and a help command reaches `HoverReducer`. If either decided
    anything on the other's vocabulary, one of these two states would not survive the other's turn.
    """
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    hover, help_ = HoverStore(ipc), HelpStore(ipc)

    assert help_.dispatch(HelpCommand.TOGGLE) == (OpenHelp(),)
    for event in STREAM:
        hover.dispatch(event)

    assert help_.current == HelpState(open=True)  # the hover stream left it alone
    assert hover.current.delays == DELAYS  # …and the help command left the hover alone


def test_a_help_turn_clears_the_hover_outbox_rather_than_leaving_it_standing() -> None:
    """A stale outbox reads to its drainer as a decision *this* turn made. The broadcast is what
    makes it reachable: the hover feature is asked to reduce a command it decides nothing about."""
    reducer = HoverReducer()
    state = reducer.reduce(HoverFeature(), HoverConfigured(DELAYS))
    state = reducer.reduce(state, HoverObserved(HoverObservation(hover=-1, word=0)))
    assert state.published  # negative control: there is something to leave behind

    assert reducer.reduce(state, HelpCommanded(HelpCommand.TOGGLE)).published == ()


def test_the_overlay_survives_an_episode_ending() -> None:
    """The retirement event is a fan-out, and "this owner has no per-episode facts" is a permanent
    and correct answer for the shortcut reference — it is the session's, not the episode's. The
    hover half is the contrast: its hysteresis is exactly an episode fact and is reset."""
    reducer = HelpReducer()
    open_ = reducer.reduce(HelpFeature(), HelpCommanded(HelpCommand.TOGGLE))

    assert reducer.reduce(open_, EpisodeRetired()).overlay == HelpState(open=True)


def test_the_same_commands_decide_the_same_overlay_with_or_without_a_reactor(request) -> None:
    """The differential `HoverStore` gets, for the feature that joined after it."""
    commands = (
        (HelpCommand.TOGGLE, 3),
        (HelpCommand.NEXT, 3),
        (HelpCommand.NEXT, 3),
        (HelpCommand.PREVIOUS, 3),
        (HelpCommand.CLOSE, 3),
        (HelpCommand.NEXT, 3),
    )
    local = HelpStore(FakeIPC())

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = HelpStore(ipc)

    assert [local.dispatch(c, page_count=n) for c, n in commands] == [
        routed.dispatch(c, page_count=n) for c, n in commands
    ]
    assert local.current == routed.current


def test_a_shrunk_document_is_folded_in_through_the_store(request) -> None:
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = HelpStore(ipc)
    store.dispatch(HelpCommand.TOGGLE)
    assert store.dispatch(HelpCommand.NEXT, page_count=9) == (ShowHelpPage(1),)

    store.repaginate(1)

    assert store.current == HelpState(open=True, page=0)
