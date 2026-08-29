"""WP5.5: the shared session-driving loop, so no feature keeps one of its own."""

from __future__ import annotations

import threading
import time

import pytest
from session_builder import build_session
from util import FakeIPC, runtime_gateway

from saitenka.app.config import ReaderOptions
from saitenka.runtime.runner import SessionRunner


def test_a_predicate_that_already_holds_takes_no_step() -> None:
    """Not an optimisation. The thing being waited for may have already happened, and a step-first
    loop would block for a wake that is never coming."""
    steps: list[float | None] = []

    assert SessionRunner(steps.append).run_until(lambda: True, deadline=1.0) is True
    assert steps == []


def test_it_steps_until_the_predicate_holds() -> None:
    steps: list[float | None] = []
    remaining = [3]

    def step(timeout: float | None) -> None:
        steps.append(timeout)
        remaining[0] -= 1

    assert SessionRunner(step).run_until(lambda: remaining[0] == 0) is True
    assert len(steps) == 3
    assert steps == [None, None, None]  # no deadline → each step blocks indefinitely


def test_each_step_is_given_the_time_that_is_left() -> None:
    """A step handed the whole timeout would overshoot the deadline by a full wake."""
    clock = [10.0]
    seen: list[float | None] = []

    def step(timeout: float | None) -> None:
        seen.append(timeout)
        clock[0] += 1.0

    SessionRunner(step, clock=lambda: clock[0]).run_until(lambda: False, deadline=13.0)

    assert seen == [3.0, 2.0, 1.0]


def test_a_passed_deadline_reports_failure_rather_than_raising() -> None:
    """The caller knows what its own timeout means — annotation raises `TimeoutError`, a capture
    might retry. Deciding that here would put one feature's policy in everyone's loop."""
    clock = [10.0]

    ran = SessionRunner(lambda _t: None, clock=lambda: clock[0]).run_until(
        lambda: False, deadline=10.0
    )

    assert ran is False


def test_a_deadline_already_past_takes_no_step() -> None:
    steps: list[float | None] = []
    clock = [50.0]

    assert (
        SessionRunner(steps.append, clock=lambda: clock[0]).run_until(lambda: False, deadline=10.0)
        is False
    )
    assert steps == []


def test_a_raising_step_is_not_swallowed() -> None:
    """A dead transport must reach the caller, not spin the loop forever."""

    def step(_timeout: float | None) -> None:
        raise OSError("pipe closed")

    with pytest.raises(OSError, match="pipe closed"):
        SessionRunner(step).run_until(lambda: False, deadline=None)


def _loop(mailbox, *, clock=time.monotonic):
    """A loop over a real mailbox, with the correlator it drives timers and completions through."""
    from saitenka.runtime.correlator import EffectCorrelator
    from saitenka.runtime.loop import SessionLoop

    class _NoCommands:
        connection_epoch = 0

        def dispatch(self, _effect) -> bool:
            return False

        def expire(self, _control) -> None:
            return None

    return SessionLoop(mailbox, EffectCorrelator(mailbox, _NoCommands(), clock=clock), clock=clock)


def test_the_loop_drives_turns_until_it_is_asked_to_stop() -> None:
    from saitenka.runtime.mailbox import SessionMailbox

    turns: list[float | None] = []
    stop = [3]

    def turn(timeout: float | None) -> bool:
        turns.append(timeout)
        stop[0] -= 1
        return True

    _loop(SessionMailbox()).run(turn, until=lambda: stop[0] == 0)

    assert turns == [None, None, None]  # nothing armed, so each turn blocks until something happens


def test_a_turn_that_reports_the_transport_gone_ends_the_loop() -> None:
    """Not expressible as a predicate over session state: nobody asked for this stop, and the
    session that would answer the predicate is the one that just lost its transport."""
    from saitenka.runtime.mailbox import SessionMailbox

    turns: list[float | None] = []

    def turn(timeout: float | None) -> bool:
        turns.append(timeout)
        return False

    _loop(SessionMailbox()).run(turn, until=lambda: False)

    assert len(turns) == 1


def test_the_loop_hands_each_payload_over_in_mailbox_sequence() -> None:
    """The push contract: a consumer sees the batch as it is popped, not after it is collected."""
    from saitenka.runtime import EventOrigin, TrafficClass
    from saitenka.runtime.events import PropertyObserved
    from saitenka.runtime.mailbox import SessionMailbox

    mailbox = SessionMailbox()
    loop = _loop(mailbox)
    for value in ("いち", "に"):
        mailbox.publish(
            PropertyObserved("sub-text", value),
            origin=EventOrigin.MPV,
            traffic=TrafficClass.NORMAL,
        )
    seen: list[object] = []

    loop.receive(0.0, seen.append)

    assert [event.data for event in seen if isinstance(event, PropertyObserved)] == ["いち", "に"]


def test_a_stop_releases_a_receiver_blocked_with_no_events_pending() -> None:
    """A stop reaches a receiver that is already blocked.

    Without this a stop is only observed when the next event happens to arrive, which for an idle
    session is never — the failure mode is a hang, so the negative control is the timeout itself.
    """
    from saitenka.runtime.mailbox import SessionMailbox

    mailbox = SessionMailbox()
    released = threading.Event()

    def receiver() -> None:
        mailbox.receive(timeout=5.0)
        released.set()

    thread = threading.Thread(target=receiver)
    thread.start()
    try:
        # Re-sent until it lands, because the wake is deliberately un-latched: it frees whoever is
        # blocked *now* and leaves no state, so one sent before the thread reaches `receive` is
        # correctly a no-op. The loop stands in for what production gets for free — the runner
        # re-tests `_stop` between receives, so a wake it misses costs one more turn, not a hang.
        deadline = time.monotonic() + 2.0
        while not released.is_set() and time.monotonic() < deadline:
            mailbox.wake()
            released.wait(0.01)
        assert released.is_set()
    finally:
        thread.join(2.0)


def test_waking_publishes_nothing_and_does_not_close() -> None:
    """A wake is not an event and not a close: the session has to stay drainable afterwards."""
    from saitenka.runtime.mailbox import SessionMailbox

    mailbox = SessionMailbox()

    mailbox.wake()

    assert mailbox.receive(timeout=0) is None
    assert mailbox.drain_ready() == ()


def test_requesting_a_stop_wakes_the_transport(request) -> None:
    """The SessionController half: the flag alone leaves a blocked receiver blocked."""

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    woken: list[bool] = []
    ipc.wake_session_runtime = lambda: woken.append(True) or True  # type: ignore[method-assign]
    reader = build_session(ipc, options=ReaderOptions().with_overrides(prefetch=False))
    try:
        reader.request_stop()
        assert woken == [True]
    finally:
        reader.close()


def test_the_claim_census_separates_what_the_reactor_owns_from_what_the_reader_still_does(
    request,
) -> None:
    """The `session-loop` duty's meter, and the reason it needs one.

    The loop already receives from the mailbox and the reactor already sees every envelope, so
    neither is what keeps the duty open — what is left is the SessionController still *acting* on the
    envelopes nothing claimed. That is invisible to the debt census, because an unclaimed envelope
    is not a debt symbol anywhere: it is a ratio, and the tail of it names the feature to migrate
    next.
    """
    from saitenka.runtime import EventOrigin, TrafficClass
    from saitenka.runtime.events import RawMpvEvent

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)

    gateway.mailbox.publish(
        RawMpvEvent({"event": "seek"}), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL
    )
    ipc.drain_events()

    census = gateway.claim_census()
    claimed, seen = census["RawMpvEvent"]
    assert seen == 1
    assert claimed == 0, "an mpv observation is still the SessionController's to act on"


def test_an_unrun_session_reports_an_empty_census_rather_than_a_clean_one(request) -> None:
    """The negative control. A session that saw nothing must not read as fully migrated — an empty
    census is a statement about the sample, and a ratio over zero would round to whatever suits."""
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)

    assert gateway.claim_census() == {}
