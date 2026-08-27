"""Keyed close-participant composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from saitenka.app.session.routes import (
    CAPABILITY_PARTICIPANTS,
    INTERACTION_WORK_PARTICIPANTS,
    WORKER_LANE_PARTICIPANTS,
)
from saitenka.app.subtitle_geometry_job import GEOMETRY_LANE

CloseAct = Callable[[], object]


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
