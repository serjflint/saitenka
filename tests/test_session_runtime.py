from __future__ import annotations

import threading
from dataclasses import dataclass, replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from saitenka.runtime import (
    DEFAULT_RUNTIME_LIMITS,
    CancelEffect,
    CloseRequested,
    CommandHandled,
    CommandOutcome,
    CommandReason,
    ConnectionLost,
    ConnectionReplaced,
    DeadlineRegistry,
    DiagnosticKind,
    DiagnosticRecord,
    EffectDeadline,
    EffectError,
    EffectFinished,
    EffectId,
    EffectOutcome,
    EmitDiagnostic,
    EventOrigin,
    ExpireEffect,
    MailboxFull,
    Owner,
    OwnerSlice,
    PropertyObserved,
    RawMpvEvent,
    ReduceResult,
    RoutedEvent,
    RouteError,
    RouteKey,
    RuntimeEvent,
    RuntimeLimits,
    ScheduleTimer,
    SessionMailbox,
    SessionReactor,
    SessionReducer,
    SessionState,
    SliceReducer,
    StopSession,
    SubmitJob,
    TimerScheduler,
    TrafficClass,
    UserCommand,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass(frozen=True, slots=True)
class State:
    observations: tuple[str, ...] = ()
    outcomes: tuple[tuple[int, EffectOutcome, EffectError | None], ...] = ()


def reduce_state(state: State, event: RuntimeEvent):
    if isinstance(event, RawMpvEvent):
        if event.name == "start":
            return state, (
                SubmitJob(EffectId(1), Owner.SUBTITLE, "cue:1", "annotation", "request"),
            )
        return replace(state, observations=(*state.observations, event.name)), ()
    if isinstance(event, EffectFinished):
        outcome = (event.effect_id.value, event.outcome, event.error)
        return replace(state, outcomes=(*state.outcomes, outcome)), ()
    return state, ()


def reduce_connection_job(state: State, event: RuntimeEvent):
    if isinstance(event, RawMpvEvent) and event.name == "start":
        return state, (
            SubmitJob(
                EffectId(1),
                Owner.SUBTITLE,
                "cue:1",
                "annotation",
                "request",
                connection_epoch=1,
            ),
        )
    return reduce_state(state, event)


def reduce_closing_reuse(state: State, event: RuntimeEvent):
    if isinstance(event, RawMpvEvent):
        return state, (
            StopSession("test"),
            SubmitJob(EffectId(1), Owner.SESSION, "session", "close", "request"),
        )
    next_state, _effects = reduce_state(state, event)
    if isinstance(event, EffectFinished):
        return next_state, (SubmitJob(EffectId(1), Owner.SESSION, "session", "close", "request"),)
    return next_state, ()


def test_mailbox_preserves_sequence_across_reserved_lanes() -> None:
    clock = Clock()
    mailbox = SessionMailbox(clock=clock)
    normal = mailbox.publish(
        RawMpvEvent("normal"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL
    )
    lifecycle = mailbox.publish(
        ConnectionReplaced(1),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )

    assert mailbox.drain_ready() == (normal, lifecycle)


def test_mailbox_limits_each_ready_turn_without_reordering() -> None:
    mailbox = SessionMailbox()
    published = tuple(
        mailbox.publish(
            RawMpvEvent(str(index)), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL
        )
        for index in range(70)
    )

    assert mailbox.receive_ready(limit=64) == published[:64]
    assert mailbox.receive_ready(limit=64) == published[64:]


def test_close_latch_preempts_a_saturated_lifecycle_lane_once() -> None:
    mailbox = SessionMailbox(lifecycle_capacity=1)
    mailbox.publish(
        ConnectionReplaced(1),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )
    first = mailbox.publish(
        CloseRequested("first"),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )
    duplicate = mailbox.publish(
        CloseRequested("duplicate"),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )

    assert duplicate is first
    assert mailbox.receive(timeout=0) == first
    assert isinstance(mailbox.receive(timeout=0).payload, ConnectionReplaced)


def test_connection_loss_has_epoch_coalesced_reserved_admission() -> None:
    mailbox = SessionMailbox(lifecycle_capacity=1)
    normal_lifecycle = mailbox.publish(
        ConnectionReplaced(1),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )
    mailbox.publish(
        ConnectionLost(1),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )
    latest = mailbox.publish(
        ConnectionLost(2),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
    )

    assert mailbox.receive_ready() == (normal_lifecycle, latest)


def test_mailbox_coalesces_only_the_closed_input_allowlist() -> None:
    mailbox = SessionMailbox()
    first_mouse = mailbox.publish(
        PropertyObserved("mouse-pos", {"x": 1}),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
        connection_epoch=1,
    )
    latest_mouse = mailbox.publish(
        PropertyObserved("mouse-pos", {"x": 2}),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
        connection_epoch=1,
    )
    property_change = mailbox.publish(
        PropertyObserved("sub-text", "\u3044\u3061"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
        connection_epoch=1,
    )

    assert mailbox.receive_ready() == (latest_mouse, property_change)
    assert first_mouse.sequence < latest_mouse.sequence


def test_mailbox_coalesces_production_scroll_names_without_losing_outcome_slots() -> None:
    mailbox = SessionMailbox(normal_capacity=4)
    mailbox.publish_command(UserCommand("saitenka-scroll-up", command_id=0), origin=EventOrigin.MPV)
    mailbox.publish_command(UserCommand("saitenka-scroll-up", command_id=1), origin=EventOrigin.MPV)

    (envelope,) = mailbox.receive_ready()
    assert envelope.payload == UserCommand("saitenka-scroll-up", command_id=1, coalesced_ids=(0,))

    assert mailbox.publish_command_terminal(
        CommandHandled(
            "saitenka-scroll-up",
            Owner.INTERACTION,
            CommandOutcome.EXECUTED,
            command_id=1,
        ),
        origin=EventOrigin.USER,
    )
    assert mailbox.publish_command_terminal(
        CommandHandled(
            "saitenka-scroll-up",
            Owner.INTERACTION,
            CommandOutcome.SUPPRESSED,
            command_id=0,
            reason=CommandReason.COALESCED,
        ),
        origin=EventOrigin.USER,
    )
    assert mailbox.snapshot.command_reserved == 0


def test_mailbox_does_not_coalesce_across_the_turn_quantum() -> None:
    mailbox = SessionMailbox()
    first = mailbox.publish(
        PropertyObserved("mouse-pos", {"x": 1}),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    second = mailbox.publish(
        PropertyObserved("mouse-pos", {"x": 2}),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )

    assert mailbox.receive_ready(limit=1) == (first,)
    assert mailbox.receive_ready(limit=1) == (second,)


def test_a_stalled_normal_lane_drops_superseded_positions_rather_than_the_session() -> None:
    """`time-pos` fills the lane whenever the drain stalls — a mine with Anki down stalls it ~55 s.

    Refusing admission there is not a bound, it is a teardown: the gateway turns `MailboxFull` into
    `CloseRequested("runtime-overloaded")` and the overlay exits. And it exits over values the
    projection publishes no delta for at all (`playback.py`, TIMING_PROPERTIES minus `sub-delay`),
    so the backlog was dead before it was refused.
    """
    mailbox = SessionMailbox(normal_capacity=4)
    kept = mailbox.publish(
        RawMpvEvent("file-loaded"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    for position in range(10):
        mailbox.publish(
            PropertyObserved("time-pos", float(position)),
            origin=EventOrigin.MPV,
            traffic=TrafficClass.NORMAL,
        )

    drained = mailbox.receive_ready()
    assert len(drained) == 4
    assert drained[0] == kept
    assert drained[-1].payload == PropertyObserved("time-pos", 9.0)
    assert mailbox.snapshot.superseded_dropped == 7


def test_terminal_reservation_survives_normal_lane_saturation() -> None:
    mailbox = SessionMailbox(normal_capacity=1, lifecycle_capacity=1, terminal_capacity=1)
    effect_id = EffectId(4)
    assert mailbox.reserve_terminal(effect_id)
    mailbox.publish(
        RawMpvEvent("one"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    try:
        mailbox.publish(
            RawMpvEvent("two"),
            origin=EventOrigin.MPV,
            traffic=TrafficClass.NORMAL,
        )
    except MailboxFull:
        pass
    else:  # pragma: no cover - bounded admission contract
        raise AssertionError("normal lane exceeded its capacity")
    completion = EffectFinished(effect_id, Owner.SESSION, "identity", EffectOutcome.SUCCEEDED)

    assert mailbox.publish_terminal(completion, origin=EventOrigin.WORKER)
    assert not mailbox.publish_terminal(completion, origin=EventOrigin.WORKER)
    assert not mailbox.reserve_terminal(effect_id)


@given(
    capacity=st.integers(min_value=1, max_value=16),
    effect_values=st.lists(
        st.integers(min_value=0, max_value=10_000), unique=True, min_size=1, max_size=32
    ),
)
def test_terminal_lane_never_accepts_more_effects_than_it_can_retire(
    capacity: int, effect_values: list[int]
) -> None:
    mailbox = SessionMailbox(terminal_capacity=capacity)
    effect_ids = [EffectId(value) for value in effect_values]
    admitted = [effect_id for effect_id in effect_ids if mailbox.reserve_terminal(effect_id)]

    assert len(admitted) == min(capacity, len(effect_ids))
    assert mailbox.snapshot.terminal_reserved == len(admitted)
    for effect_id in admitted:
        completion = EffectFinished(
            effect_id, Owner.SESSION, effect_id.value, EffectOutcome.SUCCEEDED
        )
        assert mailbox.publish_terminal(completion, origin=EventOrigin.WORKER)
    assert mailbox.snapshot.terminal == len(admitted)
    assert mailbox.snapshot.terminal_reserved == 0


def test_mailbox_publish_wakes_a_blocked_consumer() -> None:
    mailbox = SessionMailbox()
    received = []
    ready = threading.Event()

    def consume() -> None:
        ready.set()
        received.append(mailbox.receive(timeout=1.0))

    thread = threading.Thread(target=consume)
    thread.start()
    assert ready.wait(timeout=1.0)
    envelope = mailbox.publish(
        RawMpvEvent("wake"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL
    )
    thread.join(timeout=1.0)

    assert received == [envelope]
    assert not thread.is_alive()


def test_closed_mailbox_rejects_an_outstanding_terminal_reservation() -> None:
    mailbox = SessionMailbox()
    effect_id = EffectId(1)
    assert mailbox.reserve_terminal(effect_id)
    mailbox.close()
    completion = EffectFinished(effect_id, Owner.SESSION, "session", EffectOutcome.CANCELLED)

    assert not mailbox.publish_terminal(completion, origin=EventOrigin.LIFECYCLE)
    assert mailbox.snapshot.terminal_reserved == 0


def test_closed_mailbox_rejects_reserved_lifecycle_signals() -> None:
    mailbox = SessionMailbox()
    mailbox.close()

    for payload in (CloseRequested(), ConnectionLost(1)):
        try:
            mailbox.publish(
                payload,
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=1,
            )
        except MailboxFull as error:
            assert str(error) == "mailbox is closed"
        else:  # pragma: no cover - closed lifecycle contract
            raise AssertionError("closed mailbox accepted lifecycle work")
    assert mailbox.receive(timeout=0) is None


def test_named_timer_fires_once_and_cancel_is_terminal() -> None:
    scheduler = TimerScheduler()
    first = ScheduleTimer(EffectId(1), Owner.INTERACTION, "hover:1", "hover-dwell", 5.0)
    replacement = ScheduleTimer(EffectId(2), Owner.INTERACTION, "hover:2", "hover-dwell", 8.0)

    assert scheduler.schedule(first) is None
    cancelled = scheduler.schedule(replacement)
    assert cancelled == EffectFinished(
        EffectId(1), Owner.INTERACTION, "hover:1", EffectOutcome.CANCELLED, "hover-dwell"
    )
    assert scheduler.pop_due(7.0) == ()
    assert scheduler.pop_due(8.0) == (
        EffectFinished(
            EffectId(2), Owner.INTERACTION, "hover:2", EffectOutcome.SUCCEEDED, "hover-dwell"
        ),
    )
    assert scheduler.pop_due(100.0) == ()


@dataclass(frozen=True, slots=True)
class FeatureState:
    values: tuple[str, ...] = ()


def test_composed_reducer_propagates_internal_events_fifo_and_commits_atomically() -> None:
    seen_committed: list[SessionState] = []

    def playback(state: object, event: RuntimeEvent) -> ReduceResult:
        assert isinstance(state, FeatureState)
        assert isinstance(event, RawMpvEvent)
        next_state = replace(state, values=(*state.values, event.name))
        return ReduceResult(
            next_state,
            (
                RoutedEvent(Owner.SUBTITLE, UserCommand("first")),
                RoutedEvent(Owner.SUBTITLE, UserCommand("second")),
            ),
        )

    def subtitle(state: object, event: RuntimeEvent) -> ReduceResult:
        assert isinstance(state, FeatureState)
        assert isinstance(event, UserCommand)
        seen_committed.append(initial)
        return ReduceResult(replace(state, values=(*state.values, event.name)))

    initial = SessionState(
        FeatureState(), FeatureState(), FeatureState(), FeatureState(), FeatureState()
    )
    reducer = SessionReducer(
        {
            RouteKey(RawMpvEvent, Owner.PLAYBACK): playback,
            RouteKey(UserCommand, Owner.SUBTITLE): subtitle,
        }
    )

    result = reducer.reduce_turn(initial, RoutedEvent(Owner.PLAYBACK, RawMpvEvent("observed")))

    assert result.state.playback == FeatureState(("observed",))
    assert result.state.subtitle == FeatureState(("first", "second"))
    assert seen_committed == [initial, initial]


def test_composed_reducer_rejects_unowned_route() -> None:
    initial = SessionState(None, None, None, None, None)
    reducer = SessionReducer({})

    try:
        reducer.reduce_turn(initial, RoutedEvent(Owner.SUBTITLE, RawMpvEvent("wrong-owner")))
    except RouteError as error:
        assert str(error) == "no reducer for subtitle:RawMpvEvent"
    else:  # pragma: no cover - closed routing contract
        raise AssertionError("unowned route was accepted")


def test_composed_reducer_fails_closed_on_internal_event_cycle() -> None:
    def cycle(state: object, event: RuntimeEvent) -> ReduceResult:
        return ReduceResult(state, (RoutedEvent(Owner.SESSION, event),))

    initial = SessionState(None, None, None, None, None)
    reducer = SessionReducer({RouteKey(UserCommand, Owner.SESSION): cycle}, max_internal_events=2)

    try:
        reducer.reduce_turn(initial, RoutedEvent(Owner.SESSION, UserCommand("cycle")))
    except RuntimeError as error:
        assert str(error) == "runtime internal-event limit exceeded"
    else:  # pragma: no cover - quiescence contract
        raise AssertionError("internal event cycle was not bounded")


def test_the_declared_lifecycle_bound_is_the_one_the_mailbox_enforces() -> None:
    """`RuntimeLimits` has to be load-bearing, not decorative.

    It used to declare twelve bounds and enforce none, and one had already drifted — `mailbox_terminal`
    said 128 while the mailbox ran at 64 — which nothing could detect, because a limit nobody applies
    cannot disagree with anything. This fails if the policy and the lane ever separate again.
    """
    mailbox = SessionMailbox()

    for _ in range(DEFAULT_RUNTIME_LIMITS.mailbox_lifecycle):
        mailbox.publish(
            RawMpvEvent("lifecycle"), origin=EventOrigin.LIFECYCLE, traffic=TrafficClass.LIFECYCLE
        )

    assert mailbox.snapshot.lifecycle == DEFAULT_RUNTIME_LIMITS.mailbox_lifecycle
    try:
        mailbox.publish(
            RawMpvEvent("one too many"),
            origin=EventOrigin.LIFECYCLE,
            traffic=TrafficClass.LIFECYCLE,
        )
    except MailboxFull:
        pass
    else:  # pragma: no cover - the lane bound is the contract
        raise AssertionError("the lifecycle lane admitted more than the declared policy")


def test_runtime_limits_reject_nonpositive_resource_bound() -> None:
    try:
        RuntimeLimits(mailbox_turn=0)
    except ValueError as error:
        assert str(error) == "runtime limits must be positive"
    else:  # pragma: no cover - resource policy contract
        raise AssertionError("zero runtime limit was accepted")


def test_runtime_diagnostics_reject_text_bearing_labels() -> None:
    try:
        DiagnosticRecord(
            DiagnosticKind.DEGRADATION_TRANSITION,
            Owner.SUBTITLE,
            reason="provider exception includes user text",
        )
    except ValueError as error:
        assert str(error) == "diagnostic labels must be bounded identifiers"
    else:  # pragma: no cover - privacy contract
        raise AssertionError("free-form diagnostic label was accepted")


def test_timer_rejects_non_finite_deadline_and_clock() -> None:
    try:
        ScheduleTimer(EffectId(1), Owner.SESSION, "session", "deadline", float("nan"))
    except ValueError as error:
        assert str(error) == "timer deadline must be finite and non-negative"
    else:  # pragma: no cover - constructor contract
        raise AssertionError("non-finite deadline was accepted")

    scheduler = TimerScheduler()
    try:
        scheduler.pop_due(float("inf"))
    except ValueError as error:
        assert str(error) == "timer clock must be finite and non-negative"
    else:  # pragma: no cover - clock contract
        raise AssertionError("non-finite clock was accepted")


def test_target_completion_cancels_its_distinct_deadline_child() -> None:
    registry = DeadlineRegistry()
    identity = EffectDeadline(EffectId(10), 5.0)
    timer = ScheduleTimer(EffectId(11), Owner.SESSION, identity, "effect:10", 5.0)
    registry.register(EffectId(10), timer)

    control = registry.target_finished(
        EffectFinished(EffectId(10), Owner.SUBTITLE, "cue", EffectOutcome.SUCCEEDED)
    )

    assert control == CancelEffect(EffectId(11), Owner.SESSION, identity)
    assert (
        registry.timer_finished(
            EffectFinished(EffectId(11), Owner.SESSION, identity, EffectOutcome.CANCELLED)
        )
        is None
    )


def test_due_deadline_expires_only_its_target_effect() -> None:
    registry = DeadlineRegistry()
    identity = EffectDeadline(EffectId(20), 8.0)
    timer = ScheduleTimer(EffectId(21), Owner.SESSION, identity, "effect:20", 8.0)
    registry.register(EffectId(20), timer)

    control = registry.timer_finished(
        EffectFinished(EffectId(21), Owner.SESSION, identity, EffectOutcome.SUCCEEDED)
    )

    assert control == ExpireEffect(EffectId(20), 8.0)
    assert (
        registry.target_finished(
            EffectFinished(
                EffectId(20),
                Owner.SUBTITLE,
                "cue",
                EffectOutcome.FAILED,
                error=EffectError.TIMEOUT,
            )
        )
        is None
    )


def test_deadline_registry_rejects_aliased_parent_child_identity() -> None:
    registry = DeadlineRegistry()
    timer = ScheduleTimer(
        EffectId(30),
        Owner.SESSION,
        EffectDeadline(EffectId(30), 1.0),
        "effect:30",
        1.0,
    )

    try:
        registry.register(EffectId(30), timer)
    except ValueError as error:
        assert str(error) == "deadline timer must have a distinct effect ID"
    else:  # pragma: no cover - parent/child lifecycle contract
        raise AssertionError("deadline reused its target effect ID")


def test_reactor_delivers_exactly_one_terminal_outcome() -> None:
    mailbox = SessionMailbox()
    dispatched = []

    def dispatch(effect) -> bool:
        dispatched.append(effect)
        return True

    reactor = SessionReactor(State(), reduce_state, mailbox, dispatch)
    mailbox.publish(RawMpvEvent("start"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL)
    assert reactor.run_until_idle() == 1
    assert reactor.snapshot.pending_effects == (EffectId(1),)
    completion = EffectFinished(EffectId(1), Owner.SUBTITLE, "cue:1", EffectOutcome.SUCCEEDED)

    assert reactor.complete(completion, origin=EventOrigin.WORKER)
    assert not reactor.complete(completion, origin=EventOrigin.WORKER)
    assert reactor.run_until_idle() == 1
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.SUCCEEDED, None),)
    assert reactor.snapshot.pending_effects == ()


def test_completion_cannot_bypass_the_reserved_terminal_lane() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(RawMpvEvent("start"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL)
    reactor.run_until_idle()
    completion = EffectFinished(EffectId(1), Owner.SUBTITLE, "cue:1", EffectOutcome.SUCCEEDED)

    try:
        mailbox.publish(completion, origin=EventOrigin.WORKER, traffic=TrafficClass.NORMAL)
    except TypeError as error:
        assert str(error) == "effect completions must use publish_terminal"
    else:  # pragma: no cover - terminal admission contract
        raise AssertionError("completion bypassed the terminal lane")
    assert reactor.snapshot.pending_effects == (EffectId(1),)
    assert reactor.complete(completion, origin=EventOrigin.WORKER)
    reactor.run_until_idle()
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.SUCCEEDED, None),)


def test_reactor_converts_mismatched_completion_to_invalid_result() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(
        RawMpvEvent("start"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    reactor.run_until_idle()
    mismatched = EffectFinished(EffectId(1), Owner.SUBTITLE, "cue:old", EffectOutcome.SUCCEEDED)
    assert reactor.complete(mismatched, origin=EventOrigin.WORKER)

    reactor.run_until_idle()

    assert reactor.snapshot.state.outcomes == (
        (1, EffectOutcome.FAILED, EffectError.INVALID_RESULT),
    )


def test_reactor_rejects_work_without_terminal_capacity() -> None:
    mailbox = SessionMailbox(terminal_capacity=1)
    assert mailbox.reserve_terminal(EffectId(99))
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(
        RawMpvEvent("start"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )

    reactor.run_until_idle()

    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.REJECTED, EffectError.OVERLOADED),)
    assert reactor.snapshot.pending_effects == ()

    mailbox.cancel_reservation(EffectId(99))
    mailbox.publish(RawMpvEvent("start"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL)
    try:
        reactor.run_until_idle()
    except ValueError as error:
        assert str(error) == "effect ID already used: 1"
    else:  # pragma: no cover - effect lifecycle contract
        raise AssertionError("rejected effect ID was reused")


def test_reactor_reports_adapter_rejection_through_the_reserved_lane() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: False)
    mailbox.publish(
        RawMpvEvent("start"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )

    assert reactor.run_until_idle() == 2
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.REJECTED, None),)


def test_reactor_translates_unexpected_adapter_failure_once() -> None:
    mailbox = SessionMailbox()

    def fail(_effect) -> bool:
        raise RuntimeError("provider detail")

    reactor = SessionReactor(State(), reduce_state, mailbox, fail)
    mailbox.publish(
        RawMpvEvent("start"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )

    assert reactor.run_until_idle() == 2
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.FAILED, EffectError.INTERNAL),)


def test_reactor_drops_old_connection_epoch_after_replacement() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    for payload, epoch, origin, traffic in (
        (ConnectionReplaced(2), 2, EventOrigin.LIFECYCLE, TrafficClass.LIFECYCLE),
        (RawMpvEvent("old"), 1, EventOrigin.MPV, TrafficClass.NORMAL),
        (RawMpvEvent("current"), 2, EventOrigin.MPV, TrafficClass.NORMAL),
    ):
        mailbox.publish(
            payload,
            origin=origin,
            traffic=traffic,
            connection_epoch=epoch,
        )

    reactor.run_until_idle()

    assert reactor.snapshot.connection_epoch == 2
    assert reactor.snapshot.state.observations == ("current",)


def test_reactor_retires_pre_reconnect_work_as_stale() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_connection_job, mailbox, lambda _effect: True)
    mailbox.publish(
        ConnectionReplaced(1),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
        connection_epoch=1,
    )
    mailbox.publish(
        RawMpvEvent("start"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
        connection_epoch=1,
    )
    reactor.run_until_idle()
    mailbox.publish(
        ConnectionReplaced(2),
        origin=EventOrigin.LIFECYCLE,
        traffic=TrafficClass.LIFECYCLE,
        connection_epoch=2,
    )

    reactor.run_until_idle()

    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.STALE, None),)
    assert reactor.snapshot.pending_effects == ()
    assert not reactor.complete(
        EffectFinished(EffectId(1), Owner.SUBTITLE, "cue:1", EffectOutcome.SUCCEEDED),
        origin=EventOrigin.WORKER,
        connection_epoch=1,
    )


def test_close_cancels_accepted_work_and_rejects_late_completion() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(
        RawMpvEvent("start"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    reactor.run_until_idle()

    reactor.close()

    assert reactor.snapshot.lifecycle.value == "closed"
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.CANCELLED, None),)
    assert not reactor.complete(
        EffectFinished(EffectId(1), Owner.SUBTITLE, "cue:1", EffectOutcome.SUCCEEDED),
        origin=EventOrigin.WORKER,
    )


def test_close_preserves_a_terminal_outcome_already_in_the_mailbox() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(RawMpvEvent("start"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL)
    reactor.run_until_idle()
    assert reactor.complete(
        EffectFinished(EffectId(1), Owner.SUBTITLE, "cue:1", EffectOutcome.SUCCEEDED),
        origin=EventOrigin.WORKER,
    )

    reactor.close()

    assert reactor.snapshot.pending_effects == ()
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.SUCCEEDED, None),)


def test_close_discards_queued_observations_before_they_reach_policy() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(
        RawMpvEvent("queued"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )

    reactor.close()

    assert reactor.snapshot.state.observations == ()


def test_close_retires_pending_work_after_mailbox_was_closed() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_state, mailbox, lambda _effect: True)
    mailbox.publish(RawMpvEvent("start"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL)
    reactor.run_until_idle()
    mailbox.close()

    reactor.close()

    assert reactor.snapshot.pending_effects == ()
    assert reactor.snapshot.state.outcomes == ((1, EffectOutcome.CANCELLED, None),)


def test_closing_rejection_consumes_the_effect_id() -> None:
    mailbox = SessionMailbox()
    reactor = SessionReactor(State(), reduce_closing_reuse, mailbox, lambda _effect: True)
    mailbox.publish(RawMpvEvent("stop"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL)

    try:
        reactor.run_until_idle()
    except ValueError as error:
        assert str(error) == "effect ID already used: 1"
    else:  # pragma: no cover - effect lifecycle contract
        raise AssertionError("closing rejection reused an effect ID")


# --- one owner slot, several features -----------------------------------------------------------
#
# An owner slot is a single object, so the second feature to join an owner would otherwise force a
# rewrite of the first one's reducer. `SliceReducer` broadcasts, because neither available dispatch
# key is sound: by event type, several features legitimately react to the same fact; by effect
# ownership, `EffectFinished` names an `Owner` and not a feature.


def _counter(tag: str):
    """A feature reducer that records what it saw and emits one diagnostic naming itself."""

    def reduce(state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, tuple)
        return ReduceResult(
            (*state, event), effects=(EmitDiagnostic(tag, Owner.SESSION, (("saw", tag),)),)
        )

    return reduce


def test_every_feature_in_a_slot_sees_the_event_and_keeps_its_own_state() -> None:
    slice_reducer = SliceReducer({"first": _counter("first"), "second": _counter("second")})
    state = slice_reducer.initial({"first": (), "second": ()})
    event = RawMpvEvent("stop")

    result = slice_reducer(state, event)

    assert isinstance(result.state, OwnerSlice)
    # Each feature's state advanced independently — a shared slot must not merge them.
    assert result.state.get("first") == (event,)
    assert result.state.get("second") == (event,)
    assert [effect.name for effect in result.effects] == ["first", "second"]


def test_a_feature_that_ignores_an_event_does_not_lose_the_others_state() -> None:
    """The failure this exists to catch: threading the slot wrong drops the untouched feature."""

    def indifferent(state: object, _event: RuntimeEvent, /) -> ReduceResult:
        return ReduceResult(state, handled=False)

    slice_reducer = SliceReducer({"active": _counter("active"), "idle": indifferent})
    state = slice_reducer.initial({"active": (), "idle": ("untouched",)})

    result = slice_reducer(state, RawMpvEvent("stop"))

    assert isinstance(result.state, OwnerSlice)
    assert result.state.get("idle") == ("untouched",)
    assert len(result.state.get("active")) == 1


def test_a_routed_event_no_feature_handles_is_refused() -> None:
    def indifferent(state: object, _event: RuntimeEvent, /) -> ReduceResult:
        return ReduceResult(state, handled=False)

    slice_reducer = SliceReducer({"idle": indifferent})
    initial = slice_reducer.initial({"idle": ()})
    state = SessionState(initial, (), (), (), ())
    reducer = SessionReducer({RouteKey(RawMpvEvent, Owner.SESSION): slice_reducer})

    with pytest.raises(RouteError, match="no feature handled session:RawMpvEvent"):
        reducer.reduce_turn(state, RoutedEvent(Owner.SESSION, RawMpvEvent("stop")))


def test_dispatch_order_follows_registration_not_the_initial_states() -> None:
    """Effect order is observable (it is the command stream), so it must not depend on a dict
    literal written elsewhere."""
    slice_reducer = SliceReducer({"a": _counter("a"), "b": _counter("b")})

    result = slice_reducer(slice_reducer.initial({"b": (), "a": ()}), RawMpvEvent("stop"))

    assert [effect.name for effect in result.effects] == ["a", "b"]


def test_a_feature_without_an_initial_state_is_refused_at_construction() -> None:
    """Better here than as a `KeyError` on the first event of a session that already started."""
    slice_reducer = SliceReducer({"a": _counter("a"), "b": _counter("b")})

    try:
        slice_reducer.initial({"a": ()})
    except ValueError as error:
        assert "exactly one initial state" in str(error)
    else:  # pragma: no cover - slice construction contract
        raise AssertionError("slice accepted a feature with no state")


def test_installing_a_session_runtime_keeps_the_reactor_reachable() -> None:
    """Close is a state transition the reactor owns, so something has to be able to reach it.

    `install_session_runtime` used to drop `install_session_reactor`'s return value, so no entrypoint
    ever held a reactor. That is why `SessionReactor.close()` and its reject-new-work latch have been
    implemented and unreachable: `StopSession` dispatches to `close()` already, but nothing outside the
    reactor could ask for the transition, and nothing could observe that it had happened.
    """
    from util import FakeIPC

    from saitenka.app.session_routes import install_session_runtime

    gateway = install_session_runtime(FakeIPC())
    try:
        assert isinstance(gateway.session_reactor, SessionReactor)
    finally:
        gateway.close()
