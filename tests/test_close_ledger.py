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
    # The scratch dir goes last of the participants — nothing above may still write to it — and the
    # session runtime closes after even that, because its mailbox is what `_retire_artifacts`
    # announces ARTIFACTS through. A reactor closed one step earlier would reject that announcement.
    assert order[-1] == "session-runtime"
    assert order[-2] == "temporary-artifacts"


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


def test_the_runtime_closes_the_surfaces_it_was_handed() -> None:
    """The `SURFACES` phase. The Reader still *uses* the surfaces; what moved is who ends them.

    Asserted through the registration, which is the seam: a session that handed its surfaces over
    must not also close them itself, or the migration is an extra call rather than a moved
    lifetime.
    """
    from saitenka.app.session_routes import SURFACES_RESOURCE, install_session_runtime

    ipc = FakeIPC()
    gateway = install_session_runtime(ipc, startup_hint=False)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    closed: list[str] = []
    gateway.session_resources[SURFACES_RESOURCE] = _RecordingSurfaces(closed)
    try:
        reader.close()
    finally:
        gateway.close()

    assert closed == ["close"]


def test_the_surfaces_phase_closes_the_transport_after_the_removes_go_through_it() -> None:
    """Order within the phase is the contract: the overlay removes are queued *through* the
    transport, so closing it first would strand them. A tuple's order is easy to lose in a
    refactor, hence an oracle rather than a comment."""
    from saitenka.app.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close
    from saitenka.runtime.effects import CloseSessionOverlay, CloseSessionSurfaces
    from saitenka.runtime.events import ClosePhase, SessionClosing

    result = reduce_lifecycle_close(LifecycleCloseState(), SessionClosing(ClosePhase.SURFACES))

    assert [type(effect) for effect in result.effects] == [
        CloseSessionSurfaces,
        CloseSessionOverlay,
    ]


def test_a_session_with_no_runtime_still_closes_its_own_surfaces() -> None:
    """The negative control for the seam: the fallback is what makes the duty safe to migrate at
    all, so it has to be exercised, not assumed."""
    ipc = FakeIPC()  # no gateway, so no runtime owns anything
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    closed: list[str] = []
    reader.lifecycle_surfaces = _RecordingSurfaces(closed)  # type: ignore[assignment]  # local fake

    reader.close()

    assert closed == ["close"]


class _RecordingSurfaces:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def close(self) -> None:
        self._log.append("close")


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


def test_close_announces_every_phase_in_teardown_order() -> None:
    """The skeleton's whole point: phases exist up front, in one order, so a duty picks the one
    matching where its step already sits instead of inventing one.

    Order is asserted against the enum rather than a hand-written list — the enum is the
    declaration, so a reordering there that the table does not follow is the bug this catches.
    """
    from saitenka.app.session_routes import install_session_runtime
    from saitenka.runtime.events import ClosePhase, SessionClosing

    seen: list[ClosePhase] = []

    class RecordingIPC(FakeIPC):
        def deliver_runtime_event(self, event) -> bool:
            if isinstance(event, SessionClosing):
                seen.append(event.phase)
            return super().deliver_runtime_event(event)

    ipc = RecordingIPC()
    gateway = install_session_runtime(ipc, startup_hint=False)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    try:
        reader.close()
    finally:
        gateway.close()

    assert seen == sorted(seen, key=list(ClosePhase).index)
    assert set(seen) == set(ClosePhase)


def test_a_closed_session_reactor_rejects_further_work() -> None:
    """Close is a state transition, and this is the first time the session actually performs it.

    `SessionReactor.close()` and its reject-new-work latch shipped long ago, but nothing held a
    reactor and nothing emitted `StopSession`, so the transition had never run in a real session.
    The close table drives it now, as the terminal step: after it, the mailbox is closed and a
    publish is refused rather than silently queued into a session that is gone.
    """
    from util import FakeIPC

    from saitenka.app.session_routes import install_session_runtime
    from saitenka.runtime import EventOrigin, MailboxFull, RawMpvEvent, TrafficClass

    ipc = FakeIPC()
    gateway = install_session_runtime(ipc)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())

    assert reader.close().report() is None

    with pytest.raises(MailboxFull):
        gateway.mailbox.publish(
            RawMpvEvent("after-close"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL
        )
