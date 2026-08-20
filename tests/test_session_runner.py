"""WP5.5: the shared session-driving loop, so no feature keeps one of its own."""

from __future__ import annotations

import threading

import pytest
from util import FakeIPC, runtime_gateway

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


def test_a_stop_releases_a_receiver_blocked_with_no_events_pending() -> None:
    """The bound `poll_interval` currently provides, stated as a property of the mailbox instead.

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
        mailbox.wake()
        assert released.wait(2.0)
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
    """The Reader half: the flag alone leaves a blocked receiver blocked."""
    from saitenka.app.controller import Reader

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)
    reader = Reader(ipc, prefetch=False)
    woken: list[bool] = []
    reader.ipc.wake_session_runtime = lambda: woken.append(True) or True  # type: ignore[method-assign]
    try:
        reader.request_stop()
        # Read before `close`, which legitimately requests a stop of its own.
        assert reader._stop.is_set()
        assert woken == [True]
    finally:
        reader.close()
