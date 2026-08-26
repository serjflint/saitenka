"""The impure ends of the session-wide overlay and translation commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import saitenka.app.session.intents as session_intents
from saitenka.app import subtitle_modes
from saitenka.app.intents import DismissHover

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.overlay import Overlay
    from saitenka.app.subtitle_adapter import SubtitleTrackCoordinator
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.subtitle_render import SubtitleTarget


@dataclass(frozen=True, slots=True)
class SessionCommandPorts:
    """Authorities participating in overlay visibility and translation restoration."""

    overlay: Overlay
    surfaces: LifecycleSurfaces
    subtitle_pipeline: SubtitleModeCoordinator
    tooltip: TooltipController
    track: SubtitleTrackCoordinator
    translation_wanted: Callable[[], bool]
    teardown_tip: Callable[[], None]
    subtitle_target: Callable[[], SubtitleTarget]
    setup_secondary: Callable[[], int | None]
    draw_translation: Callable[[], None]


class SessionCommandCoordinator:
    """Apply session-wide overlay and subtitle conjunctions in reducer order."""

    def __init__(self, ports: SessionCommandPorts) -> None:
        self._ports = ports

    def inputs(self) -> session_intents.SessionInputs:
        ports = self._ports
        return session_intents.SessionInputs(
            overlay_visible=ports.overlay.visible,
            translation_wanted=ports.translation_wanted(),
        )

    def apply(self, effect: session_intents.SessionEffect, /) -> None:
        ports = self._ports
        if isinstance(effect, DismissHover):
            ports.tooltip.retire_selection()
            ports.teardown_tip()
        elif isinstance(effect, session_intents.SetSurfacesVisible):
            ports.surfaces.set_visible(visible=effect.visible)
        elif isinstance(effect, session_intents.ReleaseSecondarySubtitles):
            subtitle_modes.release_secondary(ports.track.ports())
        elif isinstance(effect, session_intents.SuspendSubtitles):
            ports.subtitle_pipeline.suspend_for_overlay(ports.subtitle_target())
        elif isinstance(effect, session_intents.ResumeSubtitles):
            ports.subtitle_pipeline.resume_after_overlay(ports.subtitle_target())
        elif isinstance(effect, session_intents.ShowTranslation):
            ports.setup_secondary()
            ports.draw_translation()
