"""The explicit state machine and ordered participant plan for one live session."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.session import resources as session_resources
from saitenka.app.session.close_ledger import CloseLedger, CloseStep
from saitenka.app.session.close_plan import Retirable, SessionClosePlan
from saitenka.app.session.routes import (
    COLLABORATORS_PARTICIPANT,
    DIAGNOSTICS_PARTICIPANT,
    HISTORY_PARTICIPANT,
    INPUT_PARTICIPANT,
    OBSERVERS_PARTICIPANT,
    RENDER_GUARD_PARTICIPANT,
    RENDER_SPACE_PARTICIPANT,
)
from saitenka.runtime import events

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from saitenka.app.capabilities import CapabilityProbe
    from saitenka.app.features.analysis.analysis_controller import AnalysisController
    from saitenka.app.features.annotation.annotation_controller import CueAnnotationController
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.tooltip.preparation import TooltipPreparationController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.interaction.mouse_capture import MouseCapture
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.mpvio.osd import Overlay
    from saitenka.runtime import EffectFinished


def _discard(_value: object) -> None:
    pass


class LiveState(Enum):
    NEW = auto()
    RUNNING = auto()
    CLOSING = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class StartContribution:
    name: str
    phase: events.StartPhase
    run: Callable[[], object]


@dataclass(frozen=True, slots=True)
class SessionStartActions:
    render_space: Callable[[], object]
    observers: Callable[[], object]
    input: Callable[[], object]
    collaborators: Callable[[], object]
    history: Callable[[], object]
    diagnostics: Callable[[], object]


@dataclass(frozen=True, slots=True)
class SessionLifecycleOwners:
    ipc: MpvIPC
    tts_capability: CapabilityProbe | None
    mining: MiningController
    tooltip: TooltipController
    tooltip_preparation: TooltipPreparationController
    annotation: CueAnnotationController
    analysis: AnalysisController
    mouse: MouseCapture
    geometry_refresh: Retirable
    subtitle_presentation: SubtitlePresentation
    history: HistoryOwner
    timers: LifecycleTimers
    surfaces: LifecycleSurfaces
    overlay: Overlay


@dataclass(frozen=True, slots=True)
class SessionLifecycleActs:
    render_space: Callable[[], object]
    start_observing: Callable[[], object]
    install_input: Callable[[], object]
    arm_capabilities: Callable[[], object]
    start_prefetch: Callable[[], object]
    finish_mask_atlas: Callable[[EffectFinished], None]
    history_path: Callable[[], object]
    arm_history: Callable[[float], object]
    telemetry_gauges: Callable[[], dict[str, float]]
    startup_health: Callable[[], object]
    retire_settle_window: Callable[[], None]
    finish_history: Callable[[], str | None]
    report_history: Callable[[str | None], None]


def traced_start(name: str, action: Callable[[], object]) -> Callable[[], None]:
    def run() -> None:
        with otel_metrics.traced(name):
            action()

    return run


def start_sequence(*actions: Callable[[], object]) -> Callable[[], None]:
    def run() -> None:
        for action in actions:
            action()

    return run


_START_PARTICIPANTS = {
    events.StartPhase.PROCESS: RENDER_GUARD_PARTICIPANT,
    events.StartPhase.RENDER_SPACE: RENDER_SPACE_PARTICIPANT,
    events.StartPhase.OBSERVERS: OBSERVERS_PARTICIPANT,
    events.StartPhase.INPUT: INPUT_PARTICIPANT,
    events.StartPhase.COLLABORATORS: COLLABORATORS_PARTICIPANT,
    events.StartPhase.HISTORY: HISTORY_PARTICIPANT,
    events.StartPhase.DIAGNOSTICS: DIAGNOSTICS_PARTICIPANT,
}


class SessionStartPlan:
    """Own startup contributions, runtime registration, and the fixed phase order."""

    def __init__(
        self,
        *,
        deliver: Callable[[object], bool],
        contributions: Iterable[StartContribution],
    ) -> None:
        self._deliver = deliver
        self._contributions = tuple(contributions)
        installed = {(row.phase, row.name) for row in self._contributions}
        expected = set(_START_PARTICIPANTS.items())
        if installed != expected:
            raise ValueError(
                f"startup contribution mismatch: missing={sorted(expected - installed)!r}, "
                f"unexpected={sorted(installed - expected)!r}"
            )
        self._by_phase = {row.phase: row.run for row in self._contributions}

    def registrations(self) -> tuple[tuple[str, object], ...]:
        def begin(row: StartContribution) -> Callable[[], None]:
            def run() -> None:
                row.run()

            return run

        return tuple(
            (row.name, session_resources.Starting(begin(row))) for row in self._contributions
        )

    def start(self) -> None:
        for phase in events.StartPhase:
            if not self._deliver(events.SessionStarting(phase)):
                self._by_phase[phase]()


def guard_main_render() -> None:
    from saitenka.render.banded import guard_main_render as set_guard
    from saitenka.version import overlay_version

    log.info("saitenka overlay %s starting", overlay_version())
    set_guard(on=True)


class SessionLifecycle:
    """Own startup/close order and the ``NEW -> RUNNING -> CLOSING -> CLOSED`` transition."""

    def __init__(
        self,
        *,
        startup: Iterable[Callable[[], None]],
        registrations: Iterable[tuple[str, object]] = (),
        register: Callable[[str, object], None] | None = None,
        close_steps: Callable[[], Iterable[CloseStep]],
        wake: Callable[[], None],
        stop: threading.Event | None = None,
        before_close: Callable[[], None] | None = None,
        report_close: Callable[[str], None] | None = None,
    ) -> None:
        self._startup = tuple(startup)
        self._registrations = tuple(registrations)
        self._register = register
        self._close_steps = close_steps
        self._wake = wake
        self._before_close = before_close
        self._report_close = report_close
        self._stop = stop or threading.Event()
        self._state = LiveState.NEW
        self._close_ledger: CloseLedger | None = None

    @property
    def state(self) -> LiveState:
        return self._state

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def stop_signal(self) -> threading.Event:
        """Cancellation signal shared with bounded worker-facing ports."""
        return self._stop

    def start(self) -> None:
        if self._state is LiveState.RUNNING:
            return
        if self._state is not LiveState.NEW:
            raise RuntimeError(f"cannot start a {self._state.name.lower()} session")
        try:
            if self._register is not None:
                for name, participant in self._registrations:
                    self._register(name, participant)
            for step in self._startup:
                step()
        except BaseException:
            self.close()
            raise
        self._state = LiveState.RUNNING

    def request_stop(self) -> None:
        self._stop.set()
        self._wake()

    def close(self) -> CloseLedger:
        if self._close_ledger is not None:
            return self._close_ledger
        self._state = LiveState.CLOSING
        if self._before_close is not None:
            self._before_close()
        ledger = CloseLedger()
        self._close_ledger = ledger
        ledger.run(tuple(self._close_steps()))
        self._state = LiveState.CLOSED
        report = ledger.report()
        if report is not None and self._report_close is not None:
            self._report_close(report)
        return ledger


def assemble_session_lifecycle(
    ipc: MpvIPC,
    *,
    actions: SessionStartActions,
    registrations: Iterable[tuple[str, object]],
    close_plan: SessionClosePlan,
    stop: threading.Event,
    before_close: Callable[[], None],
) -> SessionLifecycle:
    """Bind the fixed session phases to their already-owned actions."""
    start_plan = SessionStartPlan(
        deliver=ipc.deliver_runtime_event,
        contributions=(
            StartContribution(
                RENDER_GUARD_PARTICIPANT,
                events.StartPhase.PROCESS,
                guard_main_render,
            ),
            StartContribution(
                RENDER_SPACE_PARTICIPANT,
                events.StartPhase.RENDER_SPACE,
                actions.render_space,
            ),
            StartContribution(
                OBSERVERS_PARTICIPANT,
                events.StartPhase.OBSERVERS,
                actions.observers,
            ),
            StartContribution(INPUT_PARTICIPANT, events.StartPhase.INPUT, actions.input),
            StartContribution(
                COLLABORATORS_PARTICIPANT,
                events.StartPhase.COLLABORATORS,
                actions.collaborators,
            ),
            StartContribution(HISTORY_PARTICIPANT, events.StartPhase.HISTORY, actions.history),
            StartContribution(
                DIAGNOSTICS_PARTICIPANT,
                events.StartPhase.DIAGNOSTICS,
                actions.diagnostics,
            ),
        ),
    )
    all_registrations = (*registrations, *start_plan.registrations(), *close_plan.registrations())
    return SessionLifecycle(
        startup=(start_plan.start,),
        registrations=all_registrations,
        register=lambda name, participant: _discard(
            ipc.register_session_resource(name, participant)
        ),
        close_steps=close_plan.steps,
        wake=lambda: _discard(ipc.wake_session_runtime()),
        stop=stop,
        before_close=before_close,
        report_close=lambda report: log.warning("%s", report),
    )


def compose_session_lifecycle(
    owners: SessionLifecycleOwners,
    acts: SessionLifecycleActs,
    *,
    registrations: Iterable[tuple[str, object]],
    stop: threading.Event,
) -> SessionLifecycle:
    """Compose the fixed live-session lifetime from bounded owner capabilities."""

    def request_close_stop() -> None:
        stop.set()
        owners.ipc.wake_session_runtime()

    close_plan = SessionClosePlan(
        deliver=owners.ipc.deliver_runtime_event,
        request_stop=request_close_stop,
        close_lane=lambda name, timeout: owners.ipc.close_runtime_job_lane(name, timeout),
        tts_capability=owners.tts_capability,
        mining=owners.mining,
        tooltip=owners.tooltip,
        tooltip_preparation=owners.tooltip_preparation,
        annotation=owners.annotation,
        analysis=owners.analysis,
        mouse=owners.mouse,
        geometry_refresh=owners.geometry_refresh,
        retire_settle_window=acts.retire_settle_window,
        subtitle_presentation=owners.subtitle_presentation,
        history=owners.history,
        finish_history=acts.finish_history,
        report_history=acts.report_history,
        timers=owners.timers,
        surfaces=owners.surfaces,
        overlay=owners.overlay,
        close_runtime=owners.ipc.close_session_runtime,
    )
    start = SessionStartActions(
        render_space=acts.render_space,
        observers=traced_start("startup.reader_setup.observers", acts.start_observing),
        input=traced_start("startup.reader_setup.keybinds", acts.install_input),
        collaborators=start_sequence(
            owners.mining.request_seed,
            acts.arm_capabilities,
            acts.start_prefetch,
            lambda: owners.tooltip_preparation.request_mask_atlas(acts.finish_mask_atlas),
        ),
        history=lambda: owners.history.start(
            path=acts.history_path,
            arm=acts.arm_history,
        ),
        diagnostics=start_sequence(
            lambda: _install_gauge_provider(acts.telemetry_gauges),
            lambda: owners.timers.schedule(
                _startup_health_kind(),
                8.0,
                acts.startup_health,
            ),
        ),
    )
    return assemble_session_lifecycle(
        owners.ipc,
        actions=start,
        registrations=registrations,
        close_plan=close_plan,
        stop=stop,
        before_close=owners.mining.invalidate,
    )


def _install_gauge_provider(provider: Callable[[], dict[str, float]]) -> None:
    from saitenka.app import telemetry

    telemetry.set_gauge_provider(provider)


def _startup_health_kind():
    from saitenka.app.lifecycle_timers import LifecycleTimerKind

    return LifecycleTimerKind.STARTUP_HEALTH
