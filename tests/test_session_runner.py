"""WP5.5: the shared session-driving loop, so no feature keeps one of its own."""

from __future__ import annotations

import pytest

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
