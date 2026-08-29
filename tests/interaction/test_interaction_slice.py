"""`Owner.INTERACTION`'s reducers, their outboxes, and their stores.

The store test is the point of the file, as it is for subtitle: a SessionController with a session reactor and
one without must decide the same hover, because only one of the two paths is the one production
takes and the other is the one almost every test takes.

The outbox is what is new here. SUBTITLE declares, so its turns hand back nothing; INTERACTION
observes, so the reducer is what decides and the decisions have to come back.

Nine features share this slot, so the second thing the file pins is that they are independent: one
feature's events reach the others by broadcast, and each has to leave the others' state alone.
"""

from __future__ import annotations

import pytest
from session_builder import build_session
from util import FakeIPC, bare_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.routes import install_session_reactor
from saitenka.runtime.events import (
    CopyPulsed,
    CopyPulseExpired,
    EpisodeRetired,
    HelpCommanded,
    HoverConfigured,
    HoverDwellElapsed,
    HoverDwellRefused,
    HoverKanjiAdvanced,
    HoverObserved,
    HoverPauseClaimed,
    HoverPauseReleased,
    HoverScrolled,
    HoverWordForgotten,
    HoverWordResolved,
    PickerClosed,
    PickerListed,
    PickerOpened,
    PickerScrolled,
    PreviewDismissed,
    PreviewShown,
    PreviewZoomToggled,
    SidebarFollowed,
    SidebarHoldReleased,
    SidebarScrolled,
    SidebarShown,
    TipNavCleared,
    TipNavPopped,
    TipNavPushed,
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
    HoveredWordStore,
    HoverFeature,
    HoverPauseStore,
    HoverReducer,
    HoverStore,
    PickerStore,
    PreviewStore,
    PulseStore,
    SidebarStore,
    TipNavStore,
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
    gateway = bare_gateway(ipc)
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
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = HoverStore(ipc)

    with pytest.raises(RuntimeError):
        store.current = HoverFeature(HoverState(word_target=3))


def test_the_hover_view_reads_the_slice_rather_than_a_copy_of_it() -> None:
    """There is one representation of the hysteresis: what a caller is told about a dwell is what
    the machine armed, with no mirrored copy in between that can go stale."""
    from driver import Driver

    from saitenka.app.subtitle_render import NullRenderer

    ipc = FakeIPC()
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(prefetch=False, hover_switch_delay=10.0),
    )
    try:
        reader.graph.subtitle_presentation.cue.replace_tokenized(tokens=[object(), object()])
        reader.graph.tooltip.select(0)
        reader.graph.tooltip.hit = lambda *_args: 1  # type: ignore[method-assign]

        Driver(reader, instant=False).move(5, 5)

        assert reader.graph.tooltip.hover_diagnostics().word_target == 1
        assert reader.graph.tooltip.hover_view().scan_target is None
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
    gateway = bare_gateway(ipc)
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
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = HelpStore(ipc)

    assert [local.dispatch(c, page_count=n) for c, n in commands] == [
        routed.dispatch(c, page_count=n) for c, n in commands
    ]
    assert local.current == routed.current


def test_a_shrunk_document_is_folded_in_through_the_store(request) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = HelpStore(ipc)
    store.dispatch(HelpCommand.TOGGLE)
    assert store.dispatch(HelpCommand.NEXT, page_count=9) == (ShowHelpPage(1),)

    store.repaginate(1)

    assert store.current == HelpState(open=True, page=0)


# --- the slot's third feature ----------------------------------------------------------------------


def test_the_three_features_of_the_slot_do_not_read_each_other(request) -> None:
    """The broadcast is what makes this reachable: every event reaches every reducer in the slot."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    hover, help_, picker = HoverStore(ipc), HelpStore(ipc), PickerStore(ipc)

    help_.dispatch(HelpCommand.TOGGLE)
    picker.dispatch(PickerOpened())
    for event in STREAM:
        hover.dispatch(event)

    assert help_.current == HelpState(open=True)
    assert picker.current.open and picker.current.loading
    assert hover.current.delays == DELAYS


def test_a_listing_for_the_picker_that_was_up_a_moment_ago_is_refused(request) -> None:
    """The generation is the point of the machine, and the store is where a real listing meets it:
    a result that comes back after a close-and-reopen must not repopulate the picker on screen."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = PickerStore(ipc)

    store.dispatch(PickerOpened())
    stale = store.current.generation
    store.dispatch(PickerClosed())
    store.dispatch(PickerOpened())

    assert store.dispatch(PickerListed(stale, "old")) == ()
    assert store.current.listing is None
    assert store.dispatch(PickerListed(store.current.generation, "current")) != ()
    assert store.current.listing == "current"


def test_closing_a_picker_that_is_already_down_publishes_nothing(request) -> None:
    """The caller removes an overlay on the decision, so a second close must not ask for a removal
    at an id something else may since have drawn on."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = PickerStore(ipc)
    store.dispatch(PickerOpened())

    assert store.dispatch(PickerClosed()) != ()
    assert store.dispatch(PickerClosed()) == ()


def test_the_same_picker_events_decide_the_same_state_with_or_without_a_reactor(request) -> None:
    events = (
        PickerOpened(),
        PickerListed(1, "a"),
        PickerScrolled(1, 10),
        PickerScrolled(1, 10),
        PickerClosed(),
        PickerScrolled(1, 10),
    )
    local = PickerStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = PickerStore(ipc)

    assert [local.dispatch(e) for e in events] == [routed.dispatch(e) for e in events]
    assert local.current == routed.current


# --- the slot's fourth feature ---------------------------------------------------------------------


def test_a_manual_scroll_holds_auto_follow_until_the_active_row_is_visible_again(request) -> None:
    """The rule three facts argue over: the wheel, the active cue, and a re-render. A hold wins
    while the active row is off-screen, and is dropped — not merely overridden — once it is back."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = SidebarStore(ipc)
    store.dispatch(SidebarShown(active=50, capacity=10))
    store.dispatch(SidebarScrolled(steps=-5, maximum=100, held=True))
    scrolled_to = store.current.scroll

    store.dispatch(SidebarFollowed(active=51, capacity=10, geometry="same"))
    assert store.current.scroll == scrolled_to, (
        "the hold survives a cue the user scrolled away from"
    )

    store.dispatch(SidebarFollowed(active=scrolled_to + 1, capacity=10, geometry="same"))
    assert store.current.manual_hold is False, "back in view: the hold is spent, not just ignored"


def test_a_hold_that_could_not_be_armed_never_suppresses_a_follow(request) -> None:
    """Fails closed. A hold whose deadline was refused can never be released, so taking it anyway
    would suppress auto-follow for the rest of the session — the reducer decides that, not the
    caller, which is why the arming answer rides on the event."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = SidebarStore(ipc)
    store.dispatch(SidebarShown(active=50, capacity=10))

    store.dispatch(SidebarScrolled(steps=-5, maximum=100, held=False))

    assert store.current.manual_hold is False
    store.dispatch(SidebarFollowed(active=51, capacity=10, geometry="same"))
    assert store.current.scroll == 46  # followed straight away


def test_a_re_render_against_a_different_screen_overrides_the_hold(request) -> None:
    """`geometry` is opaque and only ever compared. A hold taken against one screen must not keep
    the sidebar off-target after the thing being rendered changed underneath it."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = SidebarStore(ipc)
    store.dispatch(SidebarShown(active=50, capacity=10))
    store.dispatch(SidebarScrolled(steps=-5, maximum=100, held=True))

    assert store.dispatch(SidebarFollowed(active=50, capacity=10, geometry="resized")) != ()


def test_the_hold_deadline_landing_only_clears_the_flag(request) -> None:
    """It does not move the list. Yanking rows out from under the pointer the instant a timer fires
    is the jarring version; the follow that comes with it is the caller's next act, not this one."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = SidebarStore(ipc)
    store.dispatch(SidebarShown(active=50, capacity=10))
    store.dispatch(SidebarScrolled(steps=-5, maximum=100, held=True))
    scrolled_to = store.current.scroll

    assert store.dispatch(SidebarHoldReleased()) == ()
    assert store.current.manual_hold is False
    assert store.current.scroll == scrolled_to


def test_the_same_sidebar_events_decide_the_same_state_with_or_without_a_reactor(request) -> None:
    stream = (
        SidebarShown(active=20, capacity=8),
        SidebarScrolled(steps=-2, maximum=50, held=True),
        SidebarFollowed(active=21, capacity=8, geometry="a"),
        SidebarFollowed(active=21, capacity=8, geometry="b"),
        SidebarHoldReleased(),
        SidebarFollowed(active=40, capacity=8, geometry="b"),
    )
    local = SidebarStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = SidebarStore(ipc)

    assert [local.dispatch(e) for e in stream] == [routed.dispatch(e) for e in stream]
    assert local.current == routed.current


# --- the slot's fifth feature -----------------------------------------------------------------


def test_a_pop_with_nothing_stacked_hands_back_no_decision(request) -> None:
    """Esc has to fall through to closing the tooltip, so "there is nothing to go back to" is an
    answer the slice gives rather than a check the caller makes before asking."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = TipNavStore(ipc)

    assert store.dispatch(TipNavPopped()) == ()
    assert store.current.can_go_back is False


def test_the_stack_round_trips_a_view_the_slice_never_reads(request) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = TipNavStore(ipc)
    captured = ("panel", "key", "reading")

    store.dispatch(TipNavPushed(captured))
    assert store.current.can_go_back is True

    restored = store.dispatch(TipNavPopped())
    assert [decision.view for decision in restored] == [captured]
    assert store.current.can_go_back is False


def test_clearing_leaves_nothing_to_restore(request) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = TipNavStore(ipc)
    store.dispatch(TipNavPushed("base"))

    assert store.dispatch(TipNavCleared()) == ()
    assert store.dispatch(TipNavPopped()) == ()


def test_the_back_stack_is_untouched_by_the_slot_s_other_features(request) -> None:
    """Broadcast: a hover observation reaches this reducer too. It must leave the stack alone and
    still clear its own outbox, or the next drain replays a restore that was decided a turn ago."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    nav = TipNavStore(ipc)
    hover = HoverStore(ipc)
    nav.dispatch(TipNavPushed("base"))

    hover.dispatch(HoverObserved(HoverObservation(hover=-1, word=0)))

    assert nav.current.can_go_back is True
    assert nav.dispatch(TipNavCleared()) == ()


def test_the_same_nav_events_decide_the_same_stack_with_or_without_a_reactor(request) -> None:
    stream = (
        TipNavPopped(),
        TipNavPushed("base"),
        TipNavPushed("first"),
        TipNavPopped(),
        TipNavCleared(),
        TipNavPopped(),
    )
    local = TipNavStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = TipNavStore(ipc)

    assert [local.dispatch(e) for e in stream] == [routed.dispatch(e) for e in stream]
    assert local.current == routed.current


# --- the slot's sixth and seventh features ------------------------------------------------------


def test_a_pulse_and_a_pause_claim_are_independent_of_each_other(request) -> None:
    """Both are the tooltip's, and both reach the other's reducer by broadcast. Neither may move
    the other's state, and each still clears its own outbox."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    pulse = PulseStore(ipc)
    pause = HoverPauseStore(ipc)

    pulse.dispatch(CopyPulsed(overlay=1, armed=True))
    assert pause.dispatch(HoverPauseClaimed(paused=True)) == ()

    assert pulse.current.overlay == 1
    assert pause.current.held is True
    assert pulse.dispatch(CopyPulseExpired()) != ()
    assert pause.current.held is True


def test_the_same_pulse_events_decide_the_same_slot_with_or_without_a_reactor(request) -> None:
    stream = (
        CopyPulseExpired(),
        CopyPulsed(overlay=1, armed=False),
        CopyPulsed(overlay=1, armed=True),
        CopyPulsed(overlay=2, armed=True),
        CopyPulseExpired(),
        CopyPulseExpired(),
    )
    local = PulseStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = PulseStore(ipc)

    assert [local.dispatch(e) for e in stream] == [routed.dispatch(e) for e in stream]
    assert local.current == routed.current


def test_the_same_claim_events_decide_the_same_slot_with_or_without_a_reactor(request) -> None:
    stream = (
        HoverPauseReleased(),
        HoverPauseClaimed(paused=False),
        HoverPauseReleased(),
        HoverPauseClaimed(paused=True),
        HoverPauseReleased(),
        HoverPauseReleased(),
    )
    local = HoverPauseStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = HoverPauseStore(ipc)

    assert [local.dispatch(e) for e in stream] == [routed.dispatch(e) for e in stream]
    assert local.current == routed.current


# --- the slot's eighth feature ------------------------------------------------------------------


def test_the_hovered_word_slice_declares_and_hands_nothing_back(request) -> None:
    """It observes nothing: the lookup has already answered by the time this is told. So there is
    no outbox, and a caller waiting on one would be waiting on a decision nobody makes."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = HoveredWordStore(ipc)

    assert store.dispatch(HoverWordResolved("answer")) is None
    assert store.current.meta == "answer"


def test_the_kanji_cycle_restarts_on_a_new_word_and_survives_a_revision(request) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = HoveredWordStore(ipc)
    store.dispatch(HoverWordResolved("cat"))
    store.dispatch(HoverKanjiAdvanced())
    store.dispatch(HoverKanjiAdvanced())

    store.dispatch(HoverWordResolved("cat-mined", revised=True))
    assert store.current.kanji == 2

    store.dispatch(HoverWordResolved("dog"))
    assert store.current.kanji == 0


def test_the_hovered_word_is_untouched_by_the_slot_s_other_features(request) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    word = HoveredWordStore(ipc)
    hover = HoverStore(ipc)
    word.dispatch(HoverWordResolved("cat"))

    hover.dispatch(HoverObserved(HoverObservation(hover=-1, word=0)))

    assert word.current.meta == "cat"


def test_the_same_word_events_decide_the_same_slice_with_or_without_a_reactor(request) -> None:
    stream = (
        HoverWordResolved("cat"),
        HoverKanjiAdvanced(),
        HoverWordResolved("cat-mined", revised=True),
        HoverWordForgotten(),
        HoverKanjiAdvanced(),
        HoverWordResolved("dog"),
    )
    local = HoveredWordStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = HoveredWordStore(ipc)

    for event in stream:
        local.dispatch(event)
        routed.dispatch(event)
    assert local.current == routed.current


# --- the slot's ninth feature -------------------------------------------------------------------


def test_the_preview_slice_and_its_panel_are_cut_by_lifetime(request) -> None:
    """The slice says what is composed and at what magnification; the rects and the clip's live
    process stay app-side, because a reducer can hold neither one paint's geometry nor a `Popen`."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    store = PreviewStore(ipc)

    assert store.dispatch(PreviewShown("card", "clip.opus")) is None
    assert store.current.open is True

    store.dispatch(PreviewZoomToggled())
    assert store.current.zoom is True

    store.dispatch(PreviewShown("next", None))
    assert store.current.zoom is False, "a new card does not inherit the last one's magnification"


def test_the_same_preview_events_decide_the_same_slice_with_or_without_a_reactor(request) -> None:
    stream = (
        PreviewZoomToggled(),
        PreviewShown("card", "clip.opus"),
        PreviewZoomToggled(),
        PreviewDismissed(),
        PreviewZoomToggled(),
        PreviewShown("next", None),
    )
    local = PreviewStore(FakeIPC())

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    routed = PreviewStore(ipc)

    for event in stream:
        local.dispatch(event)
        routed.dispatch(event)
    assert local.current == routed.current
