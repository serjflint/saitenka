from __future__ import annotations

from saitenka.app.session.close_ledger import CloseStep
from saitenka.app.session.lifecycle import LiveState, SessionLifecycle


def test_start_runs_the_declared_plan_once() -> None:
    started: list[str] = []
    lifecycle = SessionLifecycle(
        startup=(lambda: started.append("started"),),
        close_steps=lambda: (),
        wake=lambda: None,
    )

    lifecycle.start()
    lifecycle.start()

    assert (lifecycle.state, started) == (LiveState.RUNNING, ["started"])


def test_close_returns_the_completed_ledger_without_replaying_participants() -> None:
    retired: list[str] = []
    lifecycle = SessionLifecycle(
        startup=(),
        close_steps=lambda: (CloseStep("resource", lambda: retired.append("resource")),),
        wake=lambda: None,
    )

    first = lifecycle.close()
    second = lifecycle.close()

    assert (lifecycle.state, first is second, first.completed, retired) == (
        LiveState.CLOSED,
        True,
        ["resource"],
        ["resource"],
    )
