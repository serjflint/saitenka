"""Close must complete even when a participant raises, and must say which one did.

The motivating shape: `SessionController.close` ran its ~18 participants unguarded, so the first to raise
aborted every later one — leaving the transport open, the overlay live and the scratch dir on disk,
with a traceback naming only the thrower.
"""

from __future__ import annotations

import pytest
from session_builder import build_session as build_inert_session
from util import FakeIPC, runtime_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.session.close_ledger import CloseLedger
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import NullRenderer


def build_session(*args, **kwargs):
    session = build_inert_session(*args, **kwargs)
    session.start()
    return session


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
    """Against the real `SessionController`, so the participant list cannot drift away from the wrapper."""
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    ledger = reader.close()
    assert ledger.report() is None
    assert "transport" in ledger.completed
    assert "temporary-artifacts" in ledger.completed


def test_a_wedged_participant_is_reported_and_close_still_finishes() -> None:
    """The end-to-end claim: one broken collaborator cannot cost us the transport."""
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )

    class Wedged:
        def close(self) -> None:
            raise RuntimeError("wedged")

        def cancel_all(self) -> None:
            raise RuntimeError("wedged")

    reader.turn.tooltip_controller.surface_state().jobs = Wedged()  # type: ignore[assignment]
    ledger = reader.close()

    report = ledger.report()
    assert report is not None
    assert "interaction-jobs" in report
    assert "transport" in ledger.completed  # the point: teardown continued past the failure
    assert "temporary-artifacts" in ledger.completed


@pytest.mark.parametrize("attribute", ["lifecycle_timers", "lifecycle_surfaces"])
def test_a_late_participant_failing_does_not_lose_the_scratch_directory(attribute: str) -> None:
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    scratch = reader.turn.mining_controller._scratch_dir  # lifecycle artifact under test

    class Wedged:
        def close(self) -> None:
            raise RuntimeError("wedged")

    getattr(reader.turn, attribute).close = Wedged().close
    ledger = reader.close()

    assert ledger.report() is not None
    assert "temporary-artifacts" in ledger.completed
    assert not scratch.exists()


def test_the_lane_budget_is_armed_after_the_capabilities_come_down() -> None:
    """The 2s lane budget starts once the capabilities are down, not at table-build time.

    Hoisting it would spend that window on capability teardown, and the only symptom is lanes
    getting less time to drain on a slow machine — invisible until a close silently truncates.
    """
    ipc = FakeIPC()
    lane_timeouts: list[tuple[str, float]] = []
    ipc.close_runtime_job_lane = lambda name, timeout: bool(lane_timeouts.append((name, timeout)))
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    order = reader.close().completed

    assert order.index("capability:anki") < order.index("lanes:stop-workers")
    assert order.index("lanes:stop-workers") < order.index("lanes:subtitle-fetch")
    subtitle_fetch = next(timeout for name, timeout in lane_timeouts if name == "subtitle-fetch")
    assert 0.0 < subtitle_fetch <= 2.0


def test_every_close_participant_runs_and_keeps_its_declared_order() -> None:
    """The table is a sequence, not a set: the checker pins several pairs by order, and the
    hazards it pins are real (a geometry job admitted after its provider closed, a lane drained
    after the store it writes to went away)."""
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )

    ledger = reader.close()

    assert ledger.report() is None
    order = ledger.completed
    assert order.index("lanes:geometry") < order.index("subtitle-close")
    assert order.index("lanes:mask-atlas-startup-worker") < order.index("lanes:mask-atlas-startup")
    assert order.index("lanes:mask-atlas-startup") < order.index("lanes:mask-atlas-uninstall")
    # The scratch dir goes last of the participants — nothing above may still write to it — and the
    # session runtime closes after even that, because its mailbox is what `_retire_artifacts`
    # announces ARTIFACTS through. A reactor closed one step earlier would reject that announcement.
    assert order[-1] == "session-runtime"
    assert order[-2] == "temporary-artifacts"


# --- close participants the runtime owns ---------------------------------------------------------
#
# The `telemetry` close duty: the gauge provider is dropped by a session reducer emitting
# `DetachDiagnostics`, not by a line in `SessionController.close`. The SessionController's remaining part is announcing
# that close reached the runtime's participants.


def test_closing_a_session_detaches_the_diagnostic_gauges_through_the_runtime() -> None:
    """The duty's oracle: gauges detach with no post-close callback naming telemetry."""
    from util import runtime_gateway

    from saitenka.app import telemetry
    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
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


def test_closing_a_session_hands_the_forced_mouse_section_back_through_the_runtime() -> None:
    """The duty's oracle: the section is disabled with the SessionController's own fallback step skipped.

    Skipped, not absent — a SessionController with no runtime still forced the section, so somebody has to
    hand it back. The two must not both run: the second `disable-section` would be a write to a
    transport the close is already tearing down.
    """
    from util import runtime_gateway

    from saitenka.app import bindings
    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.turn.command_runtime.install_input()
    reader.turn.tooltip_controller.surface_state().view.rect = (0, 0, 10, 10)
    reader.turn._mouse.sync()
    try:
        assert reader.turn._mouse.held  # negative control: there is a capture to hand back
        ledger = reader.close()
    finally:
        gateway.close()

    released = [c for c in ipc.commands if c == ("disable-section", bindings.MOUSE_SECTION)]
    assert len(released) == 1  # released once — not twice, and not zero
    assert ledger.report() is None


def test_a_session_without_a_runtime_still_closes_cleanly() -> None:
    """`deliver_runtime_event` returns False rather than raising when no gateway owns the session —
    a screenshot capture and most unit tests are exactly that, and close must not care."""
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )

    ledger = reader.close()

    assert ledger.report() is None
    assert "runtime-close" in ledger.completed


def test_a_second_close_announcement_does_not_re_detach() -> None:
    """Latched on purpose: a stop racing an explicit close must not retire something an owner has
    since reinstalled. Close is idempotent by design here, not by luck."""
    from saitenka.runtime.events import SessionClosing
    from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close

    first = reduce_lifecycle_close(LifecycleCloseState(), SessionClosing())
    second = reduce_lifecycle_close(first.state, SessionClosing())

    assert first.effects  # negative control: the phase does retire something the first time
    assert second.effects == ()


def test_the_close_feature_ignores_the_events_it_shares_a_slot_with() -> None:
    """It sits in `Owner.SESSION`'s slice, which broadcasts — so every other session event reaches
    it too, and reacting to one would detach the gauges mid-session."""
    from saitenka.runtime.events import ConnectionReplaced, StartupReady
    from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close

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

    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    scratch = reader.turn.mining_controller._scratch_dir  # lifecycle artifact under test
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
    from saitenka.runtime.effects import DetachDiagnostics, RemoveSessionArtifacts
    from saitenka.runtime.events import ClosePhase, SessionClosing
    from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close

    participants = reduce_lifecycle_close(LifecycleCloseState(), SessionClosing())
    artifacts = reduce_lifecycle_close(
        participants.state, SessionClosing(ClosePhase.ARTIFACTS, "/tmp/scratch")
    )

    assert RemoveSessionArtifacts not in [type(e) for e in participants.effects]
    assert DetachDiagnostics in [type(e) for e in participants.effects]
    assert artifacts.effects == (RemoveSessionArtifacts("/tmp/scratch"),)
    # Latched per phase, not globally: a re-announced phase is a no-op, a new one is not.
    assert reduce_lifecycle_close(artifacts.state, SessionClosing()).effects == ()


def test_the_runtime_closes_the_surfaces_it_was_handed() -> None:
    """The `SURFACES` phase. The SessionController still *uses* the surfaces; what moved is who ends them.

    Asserted through the registration, which is the seam: a session that handed its surfaces over
    must not also close them itself, or the migration is an extra call rather than a moved
    lifetime.
    """
    from saitenka.app.session.routes import SURFACES_RESOURCE, install_session_runtime

    ipc = FakeIPC()
    gateway = install_session_runtime(ipc, startup_hint=False)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    closed: list[str] = []
    gateway.session_resources[SURFACES_RESOURCE] = _RecordingSurfaces(closed)
    try:
        reader.close()
    finally:
        gateway.close()

    assert closed == ["close"]


def test_the_transport_phase_follows_the_surface_phase() -> None:
    """Overlay removal must settle before the transport carrying it closes."""
    from saitenka.runtime.effects import CloseSessionOverlay, CloseSessionSurfaces
    from saitenka.runtime.events import ClosePhase, SessionClosing
    from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close

    surfaces = reduce_lifecycle_close(LifecycleCloseState(), SessionClosing(ClosePhase.SURFACES))
    overlay = reduce_lifecycle_close(surfaces.state, SessionClosing(ClosePhase.OVERLAY))

    assert [type(effect) for effect in surfaces.effects] == [CloseSessionSurfaces]
    assert [type(effect) for effect in overlay.effects] == [CloseSessionOverlay]


def test_a_session_with_no_runtime_still_closes_its_own_surfaces() -> None:
    """The negative control for the seam: the fallback is what makes the duty safe to migrate at
    all, so it has to be exercised, not assumed."""
    ipc = FakeIPC()  # no gateway, so no runtime owns anything
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    closed: list[str] = []
    reader.turn.lifecycle_surfaces.close = _RecordingSurfaces(closed).close  # type: ignore[method-assign]

    reader.close()

    assert closed == ["close"]


class _RecordingSurfaces:
    def __init__(self, log: list[str], label: str = "close") -> None:
        self._log = log
        self._label = label

    def close(self) -> None:
        self._log.append(self._label)


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
    from saitenka.app.session.routes import install_session_runtime

    monkeypatch.setattr(telemetry, "_gauge_provider", lambda: {"cache": 1.0})
    ipc = FakeIPC()
    gateway = install_session_runtime(ipc, startup_hint=startup_hint)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
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
    from saitenka.app.session.routes import install_session_runtime
    from saitenka.runtime.events import ClosePhase, SessionClosing

    seen: list[ClosePhase] = []

    class RecordingIPC(FakeIPC):
        def deliver_runtime_event(self, event) -> bool:
            if isinstance(event, SessionClosing):
                seen.append(event.phase)
            return super().deliver_runtime_event(event)

    ipc = RecordingIPC()
    gateway = install_session_runtime(ipc, startup_hint=False)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
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

    from saitenka.app.session.routes import install_session_runtime
    from saitenka.runtime import EventOrigin, MailboxFull, RawMpvEvent, TrafficClass

    ipc = FakeIPC()
    gateway = install_session_runtime(ipc)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )

    assert reader.close().report() is None

    with pytest.raises(MailboxFull):
        gateway.mailbox.publish(
            RawMpvEvent("after-close"), origin=EventOrigin.MPV, traffic=TrafficClass.NORMAL
        )


def test_the_artifacts_effect_carries_its_path_instead_of_looking_one_up() -> None:
    """Why `_retire_artifacts` may gate on the announcement while `_retire_surfaces` may not.

    `announce` reports that a reactor *saw* the event, not that anything performed the effect. That
    is why the surfaces path also checks the registration: a session with a reactor but no registered
    resource would take the True and leak the overlays. The artifacts path has no such gap only
    because `RemoveSessionArtifacts` carries the directory in the effect, so a reactor that saw it
    can always perform it.

    That is a real invariant and it was unstated. The moment this effect grows a
    `session_resources` lookup like the surfaces effects have, the announcement stops implying the
    removal and the scratch dir leaks — silently, at close, on the path nobody watches.
    """
    from dataclasses import fields
    from pathlib import Path

    from saitenka.runtime import RemoveSessionArtifacts

    assert [f.name for f in fields(RemoveSessionArtifacts)] == ["path"]

    dispatcher_source = (
        Path(__file__).resolve().parents[2] / "src/saitenka/app/session/routes.py"
    ).read_text(encoding="utf-8")
    branch = dispatcher_source.split("if isinstance(effect, RemoveSessionArtifacts):")[1]
    body = branch.split("return True")[0]
    assert "session_resources" not in body, (
        "RemoveSessionArtifacts now looks up a resource, so `_retire_artifacts` must gate on the "
        "registration the way `_retire_surfaces` does"
    )


def test_the_participants_phase_retires_the_input_capture_and_nothing_else_does() -> None:
    """Which phase owns the duty, asserted where a reordering would be invisible in a close run.

    The capture is a write to mpv, so it has to go while the transport still works — before the
    surfaces phase closes it, and before diagnostics detach in the same tuple.
    """
    from saitenka.runtime.effects import ReleaseInputCapture
    from saitenka.runtime.events import ClosePhase, SessionClosing
    from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close

    state = LifecycleCloseState()
    seen = {}
    for phase in ClosePhase:
        result = reduce_lifecycle_close(state, SessionClosing(phase))
        state = result.state
        seen[phase] = [type(effect) for effect in result.effects]

    # Second, not first: the interaction work is cancelled before the capture goes, which is the
    # order the SessionController's table had — a click routed to a cancelled job is worse than one dropped.
    assert seen[ClosePhase.PARTICIPANTS][1] is ReleaseInputCapture
    assert [p for p, kinds in seen.items() if ReleaseInputCapture in kinds] == [
        ClosePhase.PARTICIPANTS
    ]


def test_the_dispatcher_retires_the_input_capture_through_its_registered_resource() -> None:
    """The link the close run cannot show: the effect finds the capture, not the SessionController's step."""
    from util import runtime_gateway

    from saitenka.app.session.routes import INPUT_CAPTURE_RESOURCE, _dispatcher
    from saitenka.runtime.diagnostics import RuntimeLedger
    from saitenka.runtime.effects import ReleaseInputCapture

    class _Capture:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    gateway = runtime_gateway(FakeIPC())
    capture = _Capture()
    try:
        gateway.session_resources[INPUT_CAPTURE_RESOURCE] = capture
        dispatch = _dispatcher(gateway, RuntimeLedger())

        assert dispatch(ReleaseInputCapture()) is True
    finally:
        gateway.close()

    assert capture.closed == 1


def test_the_stores_phase_retires_the_session_writers_and_isolates_them() -> None:
    """One announcement, three participants — and a failing one must not strand the rest.

    `CloseLedger` gives that isolation to a step it owns. A duty that moves into the runtime is one
    announcement, so the isolation has to move with it or the migration quietly loses it.
    """
    from util import runtime_gateway

    from saitenka.app.session.resources import Retiring
    from saitenka.app.session.routes import (
        BACKLOG_RESOURCE,
        MINED_RESOURCE,
        SESSION_SUMMARY_RESOURCE,
        ResourceRetirementError,
        _dispatcher,
    )
    from saitenka.runtime.diagnostics import RuntimeLedger
    from saitenka.runtime.effects import CloseSessionStores

    def boom() -> None:
        raise RuntimeError("the backlog store is wedged")

    retired: list[str] = []
    gateway = runtime_gateway(FakeIPC())
    try:
        gateway.session_resources[SESSION_SUMMARY_RESOURCE] = Retiring(
            lambda: retired.append("summary")
        )
        gateway.session_resources[BACKLOG_RESOURCE] = Retiring(boom)
        gateway.session_resources[MINED_RESOURCE] = Retiring(lambda: retired.append("mined"))
        dispatch = _dispatcher(gateway, RuntimeLedger())

        with pytest.raises(ResourceRetirementError) as raised:
            dispatch(CloseSessionStores())
    finally:
        gateway.close()

    assert retired == ["summary", "mined"]  # the one behind the failure still ran
    assert [name for name, _error in raised.value.failed] == [BACKLOG_RESOURCE]


def test_a_runtime_resource_failure_reaches_the_returned_close_ledger() -> None:
    """A runtime-owned failure is visible without closing its successful peers twice."""
    from util import runtime_gateway

    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    retired: list[str] = []

    def fail_backlog() -> None:
        raise RuntimeError("backlog close failed")

    reader.turn.history.close_backlog = fail_backlog  # type: ignore[method-assign]
    reader.turn.mining_controller.close_store = lambda: retired.append("mined")  # type: ignore[method-assign]
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert [failure.participant for failure in ledger.failures] == ["phase:stores"]
    assert retired == ["mined"]


def test_a_failing_close_effect_does_not_skip_the_rest_of_its_phase() -> None:
    """Isolation spans effect boundaries, not only peers retired by the same effect."""
    from saitenka.app.session.resources import Retiring
    from saitenka.app.session.routes import (
        INPUT_CAPTURE_RESOURCE,
        INTERACTION_WORK_PARTICIPANTS,
        install_session_reactor,
    )

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    retired: list[str] = []

    def fail_interaction() -> None:
        raise KeyboardInterrupt

    gateway.session_resources[INTERACTION_WORK_PARTICIPANTS[0]] = Retiring(fail_interaction)
    gateway.session_resources[INTERACTION_WORK_PARTICIPANTS[1]] = Retiring(
        lambda: retired.append("metadata")
    )
    gateway.session_resources[INPUT_CAPTURE_RESOURCE] = Retiring(lambda: retired.append("capture"))
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert retired == ["metadata", "capture"]
    assert [failure.participant for failure in ledger.failures] == ["runtime-close"]


def test_a_failing_surface_remove_does_not_skip_the_overlay_transport() -> None:
    from saitenka.app.session.resources import Retiring
    from saitenka.app.session.routes import (
        OVERLAY_RESOURCE,
        SURFACES_RESOURCE,
        install_session_reactor,
    )

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    retired: list[str] = []

    def fail_surfaces() -> None:
        raise RuntimeError("surface close failed")

    gateway.session_resources[SURFACES_RESOURCE] = Retiring(fail_surfaces)
    gateway.session_resources[OVERLAY_RESOURCE] = Retiring(lambda: retired.append("overlay"))
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert retired == ["overlay"]
    assert [failure.participant for failure in ledger.failures] == ["lifecycle-surfaces"]


def test_a_missing_surface_resource_falls_back_before_the_overlay_transport_closes() -> None:
    from saitenka.app.session.routes import (
        OVERLAY_RESOURCE,
        SURFACES_RESOURCE,
        install_session_reactor,
    )

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    order: list[str] = []
    reader.turn.lifecycle_surfaces.close = _RecordingSurfaces(  # type: ignore[method-assign]
        order, "surfaces"
    ).close
    gateway.session_resources[OVERLAY_RESOURCE] = _RecordingSurfaces(order, "overlay")
    del gateway.session_resources[SURFACES_RESOURCE]
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert order == ["surfaces", "overlay"]
    assert [failure.participant for failure in ledger.failures] == ["lifecycle-surfaces"]


def test_a_missing_runtime_resource_is_reported_as_refused_close_work() -> None:
    from saitenka.app.session.routes import INPUT_CAPTURE_RESOURCE, install_session_reactor
    from saitenka.runtime.reactor import LifecycleEffectError

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    released: list[str] = []

    reader.turn._mouse.release = lambda: released.append("capture")  # type: ignore[method-assign]
    del gateway.session_resources[INPUT_CAPTURE_RESOURCE]
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert [failure.participant for failure in ledger.failures] == ["runtime-close"]
    failure = ledger.failures[0].error
    assert isinstance(failure, LifecycleEffectError)
    assert any("input-capture: missing" in str(error) for _effect, error in failure.failures)
    assert released == ["capture"]


def test_a_runtime_owned_session_closes_its_stores_exactly_once() -> None:
    """SessionController keeps the store steps as no-runtime fallbacks; both paths must not run."""
    from util import runtime_gateway

    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    closed: list[str] = []
    reader.turn.history.close_backlog = lambda: closed.append("backlog")  # type: ignore[method-assign]
    reader.turn.mining_controller.close_store = lambda: closed.append("mined")  # type: ignore[method-assign]
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert closed == ["backlog", "mined"]
    assert ledger.report() is None


def test_a_runtime_owned_session_closes_the_subtitle_raster_exactly_once() -> None:
    """The SessionController keeps the three steps as the no-runtime fallback; both paths must not run."""
    from util import runtime_gateway

    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    closed: list[str] = []
    reader.turn.subtitle_presentation.clear_pixels = lambda: closed.append("clear")  # type: ignore[method-assign]
    reader.turn.subtitle_presentation.close_raster = lambda: closed.append("close")  # type: ignore[method-assign]
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert closed == ["clear", "close"]
    assert ledger.report() is None


def test_every_migrated_phase_retires_something_and_no_two_share_a_participant() -> None:
    """The whole close table, as one assertion: a duty lands in exactly one phase.

    Two phases naming the same resource would retire it twice — once early, against collaborators
    that are still live — and no single-duty test can see that.
    """
    from saitenka.app.session.routes import _RESOURCE_OF
    from saitenka.runtime.events import ClosePhase, SessionClosing
    from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close

    state = LifecycleCloseState()
    seen: list[str] = []
    for phase in ClosePhase:
        result = reduce_lifecycle_close(state, SessionClosing(phase))
        state = result.state
        for effect in result.effects:
            seen.extend(_RESOURCE_OF.get(type(effect), ()))

    assert len(seen) == len(set(seen))


def test_a_gateway_without_a_reactor_still_runs_every_close_participant() -> None:
    """Registration is not performance, and the fallback has to know the difference.

    A session with a gateway but no reactor registers every participant and runs none of them, so a
    fallback gated on registration alone is skipped by a runtime that never acted — a close that
    leaves lanes draining and stores open, silently. The phase's announcement is what answers,
    which is why it runs before the steps it may replace.
    """
    from util import runtime_gateway

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)  # a gateway, deliberately without `install_session_reactor`
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    lanes: list[str] = []
    ipc.close_runtime_job_lane = lambda name, _timeout: bool(lanes.append(name)) or True
    try:
        ledger = reader.close()
    finally:
        gateway.close()

    assert "subtitle-fetch" in lanes  # the SessionController ran them itself
    assert ledger.report() is None


def test_every_registered_participant_is_named_by_an_effect_and_the_reverse() -> None:
    """The verb tables and the registrations have to agree, and nothing else checks that they do.

    A name registered but never named by an effect is a participant the runtime silently never
    retires — the SessionController's fallback carries it forever and the duty reads as migrated. A name in a
    table but never registered is the mirror: `_retire` answers False and the phase reports a
    failure nobody caused.
    """
    from saitenka.app.session.routes import _PARTICIPANT_OF, _PERFORMER_OF, _RESOURCE_OF

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    try:
        registered = set(gateway.session_resources)
    finally:
        reader.close()
        gateway.close()

    named = (
        {name for names in _RESOURCE_OF.values() for name in names}
        | set(_PARTICIPANT_OF.values())
        | set(_PERFORMER_OF.values())
    )

    assert registered == named


def test_every_lane_the_session_opens_the_session_closes_by_name() -> None:
    """A lane left out of `WORKER_LANE_PARTICIPANTS` is not left open — the gateway's blanket
    `JobBroker.close()` reaches it — but only *after* the session's close has finished, so its
    workers run on against collaborators the phases behind it have torn down. Closing by name is
    what puts the cancellation inside the session's own teardown.

    Asserted as a subset, not an equality: the table also carries steps that close a feature rather
    than a lane, and a session built with `prefetch=False` opens fewer lanes than a full one.
    """
    opened: list[str] = []
    closed: list[str] = []

    class RecordingIPC(FakeIPC):
        def register_runtime_job_lane(self, name, policy, handler) -> bool:
            accepted = super().register_runtime_job_lane(name, policy, handler)
            if accepted:
                opened.append(name)
            return accepted

        def close_runtime_job_lane(self, name, timeout=2.0) -> bool:
            closed.append(name)
            return super().close_runtime_job_lane(name, timeout)

    ipc = RecordingIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    try:
        reader.close()
    finally:
        gateway.close()

    assert opened, "the session opened no lanes at all — the oracle would pass vacuously"
    assert set(opened) - set(closed) == set()
