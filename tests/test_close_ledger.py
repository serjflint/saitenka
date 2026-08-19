"""Close must complete even when a participant raises, and must say which one did.

The motivating shape: `Reader.close` ran its ~18 participants unguarded, so the first to raise
aborted every later one — leaving the transport open, the overlay live and the scratch dir on disk,
with a traceback naming only the thrower.
"""

from __future__ import annotations

import pytest
from util import FakeIPC

from saitenka.app.close_ledger import CloseLedger
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer


def test_a_failing_participant_does_not_strand_the_ones_after_it() -> None:
    ledger = CloseLedger()
    order: list[str] = []

    with ledger.participant("first"):
        order.append("first")
    with ledger.participant("thrower"):
        order.append("thrower")
        raise RuntimeError("boom")
    with ledger.participant("last"):
        order.append("last")

    assert order == ["first", "thrower", "last"]
    assert ledger.completed == ["first", "last"]
    assert [failure.participant for failure in ledger.failures] == ["thrower"]


def test_a_clean_close_reports_nothing() -> None:
    """The negative control: `report` must be able to say "fine", or it is not an oracle."""
    ledger = CloseLedger()
    with ledger.participant("only"):
        pass
    assert ledger.report() is None
    assert ledger.completed == ["only"]


def test_the_report_names_every_failure_and_its_type() -> None:
    ledger = CloseLedger()
    for name, error in (("a", RuntimeError), ("b", ValueError)):
        with ledger.participant(name):
            raise error("x")
    report = ledger.report()
    assert report is not None
    assert "a (RuntimeError)" in report
    assert "b (ValueError)" in report


def test_an_interrupt_during_teardown_still_runs_the_rest() -> None:
    """A Ctrl-C mid-close is exactly when the remaining participants matter most.

    `BaseException` rather than `Exception` is therefore deliberate — re-raising here would strand
    the transport and the scratch dir, which is the bug this exists to prevent.
    """
    ledger = CloseLedger()
    reached = False

    with ledger.participant("interrupted"):
        raise KeyboardInterrupt
    with ledger.participant("after"):
        reached = True

    assert reached
    assert ledger.completed == ["after"]
    assert [type(failure.error) for failure in ledger.failures] == [KeyboardInterrupt]


def test_close_returns_a_clean_ledger_for_a_healthy_reader() -> None:
    """Against the real `Reader`, so the participant list cannot drift away from the wrapper."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
    ledger = reader.close()
    assert ledger.report() is None
    assert "transport" in ledger.completed
    assert "temporary-artifacts" in ledger.completed


def test_a_wedged_participant_is_reported_and_close_still_finishes() -> None:
    """The end-to-end claim: one broken collaborator cannot cost us the transport."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    class Wedged:
        def close(self) -> None:
            raise RuntimeError("wedged")

        def cancel_all(self) -> None:
            raise RuntimeError("wedged")

    reader._interaction_jobs = Wedged()  # type: ignore[assignment]
    ledger = reader.close()

    report = ledger.report()
    assert report is not None
    assert "interaction-jobs" in report
    assert "transport" in ledger.completed  # the point: teardown continued past the failure
    assert "temporary-artifacts" in ledger.completed


@pytest.mark.parametrize("attribute", ["lifecycle_timers", "lifecycle_surfaces"])
def test_a_late_participant_failing_does_not_lose_the_scratch_directory(attribute: str) -> None:
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())
    scratch = reader._tmp

    class Wedged:
        def close(self) -> None:
            raise RuntimeError("wedged")

    setattr(reader, attribute, Wedged())
    ledger = reader.close()

    assert ledger.report() is not None
    assert "temporary-artifacts" in ledger.completed
    assert not scratch.exists()


def test_the_lane_budget_is_armed_after_the_capabilities_come_down() -> None:
    """The 2s lane budget starts once the capabilities are down, not at table-build time.

    Hoisting it would spend that window on capability teardown, and the only symptom is lanes
    getting less time to drain on a slow machine — invisible until a close silently truncates.
    """
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    order = reader.close().completed

    assert order.index("stop-workers") < order.index("lane-budget")
    assert order.index("lane-budget") < order.index("lane:subtitle-fetch")


def test_every_close_participant_runs_and_keeps_its_declared_order() -> None:
    """The table is a sequence, not a set: the checker pins several pairs by order, and the
    hazards it pins are real (a geometry job admitted after its provider closed, a lane drained
    after the store it writes to went away)."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    ledger = reader.close()

    assert ledger.report() is None
    order = ledger.completed
    assert order.index("lane:geometry") < order.index("subtitle-close")
    assert order.index("mask-atlas-startup") < order.index("lane:mask-atlas-startup")
    assert order.index("lane:mask-atlas-startup") < order.index("mask-atlas-uninstall")
    assert order[-1] == "temporary-artifacts"


# --- close participants the runtime owns ---------------------------------------------------------
#
# The `telemetry` close duty: the gauge provider is dropped by a session reducer emitting
# `DetachDiagnostics`, not by a line in `Reader.close`. The Reader's remaining part is announcing
# that close reached the runtime's participants.


def test_closing_a_session_detaches_the_diagnostic_gauges_through_the_runtime() -> None:
    """The duty's oracle: gauges detach with no post-close callback naming telemetry."""
    from util import runtime_gateway

    from saitenka.app import telemetry
    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    telemetry.set_gauge_provider(lambda: {"panel_cache.size": 1.0})
    try:
        assert telemetry._gauge_provider is not None  # negative control: the oracle can fail
        ledger = reader.close()
        detached = telemetry._gauge_provider is None
    finally:
        telemetry.set_gauge_provider(None)
        gateway.close()

    assert detached
    assert "runtime-close" in ledger.completed
    assert ledger.report() is None


def test_a_session_without_a_runtime_still_closes_cleanly() -> None:
    """`deliver_runtime_event` returns False rather than raising when no gateway owns the session —
    a screenshot capture and most unit tests are exactly that, and close must not care."""
    reader = Reader(FakeIPC(), prefetch=False, renderer=NullRenderer())

    ledger = reader.close()

    assert ledger.report() is None
    assert "runtime-close" in ledger.completed


def test_a_second_close_announcement_does_not_re_detach() -> None:
    """Latched on purpose: a stop racing an explicit close must not retire something an owner has
    since reinstalled. Close is idempotent by design here, not by luck."""
    from saitenka.app.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close
    from saitenka.runtime.events import SessionClosing

    first = reduce_lifecycle_close(LifecycleCloseState(), SessionClosing())
    second = reduce_lifecycle_close(first.state, SessionClosing())

    assert len(first.effects) == 1
    assert second.effects == ()


def test_the_close_feature_ignores_the_events_it_shares_a_slot_with() -> None:
    """It sits in `Owner.SESSION`'s slice, which broadcasts — so every other session event reaches
    it too, and reacting to one would detach the gauges mid-session."""
    from saitenka.app.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close
    from saitenka.runtime.events import ConnectionReplaced, StartupReady

    state = LifecycleCloseState()
    for event in (StartupReady(), ConnectionReplaced(1)):
        result = reduce_lifecycle_close(state, event)
        assert result.effects == ()
        assert result.state is state


def test_the_runtime_removes_the_scratch_directory_when_it_owns_the_session() -> None:
    """The `ARTIFACTS` phase, which runs once nothing can still write.

    Scope, because the fallback makes it easy to overclaim: this pins that the directory is gone
    after close with a runtime installed, NOT that the runtime is what removed it — `_retire_artifacts`
    falls back to its own `rmtree` whenever the announcement goes unclaimed, so both paths look
    identical from here. What the runtime uniquely does is asserted on the gauge detach below.
    """
    from util import runtime_gateway

    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    scratch = reader._tmp
    assert scratch.exists()  # negative control
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert not scratch.exists()
    assert ledger.report() is None


def test_the_artifacts_phase_is_separate_from_the_participants_phase() -> None:
    """Close is a sequence: the scratch dir goes only after everything that writes to it stopped,
    so one announcement for both phases would delete it while lanes were still draining."""
    from saitenka.app.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close
    from saitenka.runtime.effects import DetachDiagnostics, RemoveSessionArtifacts
    from saitenka.runtime.events import ClosePhase, SessionClosing

    participants = reduce_lifecycle_close(LifecycleCloseState(), SessionClosing())
    artifacts = reduce_lifecycle_close(
        participants.state, SessionClosing(ClosePhase.ARTIFACTS, "/tmp/scratch")
    )

    assert [type(effect) for effect in participants.effects] == [DetachDiagnostics]
    assert artifacts.effects == (RemoveSessionArtifacts("/tmp/scratch"),)
    # Latched per phase, not globally: a re-announced phase is a no-op, a new one is not.
    assert reduce_lifecycle_close(artifacts.state, SessionClosing()).effects == ()


@pytest.mark.parametrize("startup_hint", [True, False])
def test_composing_a_session_runtime_leaves_its_close_duties_reachable(
    monkeypatch, *, startup_hint: bool
) -> None:
    """`attach` installed a gateway but no reactor, so `Owner.SESSION`'s close duties had nobody to
    run them — its gauge provider would have outlived the session it belonged to.

    Driven through the one helper both entrypoints now call, so neither can regain a half-wired
    session. Parametrised over the breadcrumb because that flag is the only thing they differ on,
    and it must not gate whether the reactor exists.

    The oracle is the gauge detach, deliberately *not* the scratch dir: `_retire_artifacts` falls
    back to its own `rmtree` when nothing claims the announcement, so the directory is gone either
    way and cannot tell a wired session from a half-wired one. Nothing detaches the gauge but the
    reactor.
    """
    from saitenka.app import telemetry
    from saitenka.app.session_routes import install_session_runtime

    monkeypatch.setattr(telemetry, "_gauge_provider", lambda: {"cache": 1.0})
    ipc = FakeIPC()
    gateway = install_session_runtime(ipc, startup_hint=startup_hint)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert telemetry._gauge_provider is None
    assert ledger.report() is None
