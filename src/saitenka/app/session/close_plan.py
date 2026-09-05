"""The complete, ordered retirement plan for one live session."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app.features.mining.mining_operation import LANE as MINING_OPERATION_LANE
from saitenka.app.features.tooltip.preparation import TOOLTIP_PREPARATION_CLOSE_PARTICIPANTS
from saitenka.app.session.close_ledger import CloseStep
from saitenka.app.subtitle_geometry_job import GEOMETRY_LANE

if TYPE_CHECKING:
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
    from saitenka.mpvio.osd import Overlay

CloseAct = Callable[[], object]

CAPABILITY_PARTICIPANTS = ("capability:tts", "capability:anki")
INTERACTION_WORK_PARTICIPANTS = ("interaction-jobs", "hover-metadata")
WORKER_LANE_PARTICIPANTS = (
    "lanes:stop-workers",
    "lanes:subtitle-fetch",
    "lanes:subtitle-picker",
    "lanes:geometry",
    "lanes:annotation",
    "lanes:cue-annotation",
    "lanes:tooltip-raster",
    "lanes:tooltip-render-ahead",
    "lanes:tooltip-engaged-worker",
    "lanes:tooltip-engaged",
    *TOOLTIP_PREPARATION_CLOSE_PARTICIPANTS,
    "lanes:capabilities",
    "lanes:interaction-metadata",
    "lanes:mining-operation",
    "lanes:mined-seed",
    "lanes:episode-analysis",
    "lanes:render-pool",
)


class Retirable(Protocol):
    def retire(self) -> object: ...


@dataclass(frozen=True, slots=True)
class CloseContributions:
    close_tts: CloseAct
    close_anki: CloseAct
    cancel_interaction_jobs: CloseAct
    close_hover_metadata: CloseAct
    start_lane_budget: CloseAct
    close_lane: Callable[[str, float], object]
    lane_remaining: Callable[[], float]
    close_annotation: CloseAct
    close_tooltip_raster: CloseAct
    close_tooltip_engaged: CloseAct
    tooltip_preparation: Mapping[str, CloseAct]
    close_analysis: CloseAct
    close_render_pool: CloseAct


def assemble_close_participants(contributions: CloseContributions) -> dict[str, CloseAct]:
    """Bind every declaration by name so ordering changes cannot retarget an act."""

    preparation_names = set(contributions.tooltip_preparation)
    expected_preparation_names = set(TOOLTIP_PREPARATION_CLOSE_PARTICIPANTS)
    if preparation_names != expected_preparation_names:
        raise RuntimeError(
            "tooltip preparation close contribution mismatch: "
            f"missing={sorted(expected_preparation_names - preparation_names)!r}, "
            f"unexpected={sorted(preparation_names - expected_preparation_names)!r}"
        )

    def lane(name: str) -> CloseAct:
        return lambda: contributions.close_lane(name, contributions.lane_remaining())

    participants: dict[str, CloseAct] = {
        "capability:tts": contributions.close_tts,
        "capability:anki": contributions.close_anki,
        "interaction-jobs": contributions.cancel_interaction_jobs,
        "hover-metadata": contributions.close_hover_metadata,
        "lanes:stop-workers": contributions.start_lane_budget,
        "lanes:subtitle-fetch": lane("subtitle-fetch"),
        "lanes:subtitle-picker": lane("subtitle-picker"),
        "lanes:geometry": lane(GEOMETRY_LANE),
        "lanes:annotation": contributions.close_annotation,
        "lanes:cue-annotation": lane("cue-annotation"),
        "lanes:tooltip-raster": contributions.close_tooltip_raster,
        "lanes:tooltip-render-ahead": lane("tooltip-render-ahead"),
        "lanes:tooltip-engaged-worker": contributions.close_tooltip_engaged,
        "lanes:tooltip-engaged": lane("tooltip-engaged"),
        **contributions.tooltip_preparation,
        "lanes:capabilities": lane("capabilities"),
        "lanes:interaction-metadata": lane("interaction-metadata"),
        "lanes:mining-operation": lane(MINING_OPERATION_LANE),
        "lanes:mined-seed": lane("mined-seed"),
        "lanes:episode-analysis": contributions.close_analysis,
        "lanes:render-pool": contributions.close_render_pool,
    }
    declared = CAPABILITY_PARTICIPANTS + INTERACTION_WORK_PARTICIPANTS + WORKER_LANE_PARTICIPANTS
    missing = set(declared).difference(participants)
    unexpected = set(participants).difference(declared)
    if missing or unexpected:
        raise RuntimeError(
            f"close participant mismatch: missing={sorted(missing)!r}, "
            f"unexpected={sorted(unexpected)!r}"
        )
    return participants


class SessionClosePlan:
    """Own every retirement participant and the one global teardown order.

    The plan is deliberately close-specific.  It receives the resources it retires instead of a
    session host, so adding a feature API to the live controller cannot silently make it available
    during teardown.
    """

    def __init__(  # noqa: PLR0913 -- explicit retirement resources are the contract
        self,
        *,
        request_stop: Callable[[], None],
        close_lane: Callable[[str, float], object],
        tts_capability: CapabilityProbe | None,
        mining: MiningController,
        tooltip: TooltipController,
        tooltip_preparation: TooltipPreparationController,
        annotation: CueAnnotationController,
        analysis: AnalysisController,
        mouse: MouseCapture,
        geometry_refresh: Retirable,
        retire_settle_window: Callable[[], None],
        subtitle_presentation: SubtitlePresentation,
        history: HistoryOwner,
        finish_history: Callable[[], str | None],
        report_history: Callable[[str | None], None],
        timers: LifecycleTimers,
        surfaces: LifecycleSurfaces,
        overlay: Overlay,
        detach_diagnostics: CloseAct,
        close_runtime: Callable[[], object],
    ) -> None:
        self._request_stop = request_stop
        self._close_lane = close_lane
        self._tts_capability = tts_capability
        self._mining = mining
        self._tooltip = tooltip
        self._tooltip_preparation = tooltip_preparation
        self._annotation = annotation
        self._analysis = analysis
        self._mouse = mouse
        self._geometry_refresh = geometry_refresh
        self._retire_settle_window = retire_settle_window
        self._subtitle_presentation = subtitle_presentation
        self._history = history
        self._finish_history = finish_history
        self._report_history = report_history
        self._timers = timers
        self._surfaces = surfaces
        self._overlay = overlay
        self._detach_diagnostics = detach_diagnostics
        self._close_runtime = close_runtime
        self._lane_deadline = 0.0
        self._participants = self._build_participants()

    def steps(self) -> tuple[CloseStep, ...]:
        return (
            *self._participant_steps(CAPABILITY_PARTICIPANTS),
            *self._participant_steps(INTERACTION_WORK_PARTICIPANTS),
            CloseStep("mouse-capture", self._mouse.release),
            CloseStep("diagnostics", self._detach_diagnostics),
            *self._participant_steps(WORKER_LANE_PARTICIPANTS),
            CloseStep("geometry-refresh", self._geometry_refresh.retire),
            CloseStep("settle-window", self._retire_settle_window),
            CloseStep("subtitle-deactivate", self._subtitle_presentation.deactivate),
            CloseStep(
                "subtitle-clear",
                self._subtitle_presentation.clear_pixels,
                lambda: self._subtitle_presentation.native,
            ),
            CloseStep("subtitle-close", self._subtitle_presentation.close_raster),
            CloseStep("session-stats", lambda: self._report_history(self._finish_history())),
            CloseStep("backlog-store", self._history.close_backlog, lambda: self._history.backlog),
            CloseStep("mined-store", self._mining.close_store),
            CloseStep("lifecycle-timers", self._timers.close),
            CloseStep("lifecycle-surfaces", self._surfaces.close),
            CloseStep("transport", self._overlay.close),
            CloseStep("render-guard", self._release_main_render),
            CloseStep("temporary-artifacts", self._retire_artifacts),
            CloseStep("session-runtime", self._close_runtime),
        )

    def _participant_steps(self, names: tuple[str, ...]) -> tuple[CloseStep, ...]:
        return tuple(CloseStep(name, self._participants[name]) for name in names)

    def _remaining(self) -> float:
        return max(0.0, self._lane_deadline - time.monotonic())

    def _build_participants(self) -> dict[str, CloseAct]:
        def start_lane_budget() -> None:
            self._request_stop()
            self._lane_deadline = time.monotonic() + 2.0

        def close_render_pool() -> None:
            from saitenka.parallel import shutdown_shared_executor

            shutdown_shared_executor(wait=False)

        return assemble_close_participants(
            CloseContributions(
                close_tts=lambda: (
                    self._tts_capability.close() if self._tts_capability is not None else None
                ),
                close_anki=lambda: self._mining.close_capability(),
                cancel_interaction_jobs=lambda: self._tooltip.cancel_jobs(),
                close_hover_metadata=lambda: self._tooltip.close_metadata(),
                start_lane_budget=start_lane_budget,
                close_lane=self._close_lane,
                lane_remaining=self._remaining,
                close_annotation=lambda: self._annotation.close(),
                close_tooltip_raster=lambda: self._tooltip.close_render_ahead(),
                close_tooltip_engaged=lambda: self._tooltip.close_engaged(),
                tooltip_preparation=self._tooltip_preparation.close_participants(
                    self._close_lane, self._remaining
                ),
                close_analysis=lambda: self._analysis.close_lane(self._remaining()),
                close_render_pool=close_render_pool,
            )
        )

    def _retire_artifacts(self) -> None:
        self._mining.retire_artifacts()

    @staticmethod
    def _release_main_render() -> None:
        from saitenka.render.banded import guard_main_render

        guard_main_render(on=False)
