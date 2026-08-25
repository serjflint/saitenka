"""The impure ends of the session-wide overlay and translation commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import saitenka.app.session.intents as session_intents
from saitenka.app import subtitle_modes
from saitenka.app.intents import DismissHover

if TYPE_CHECKING:
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.overlay import Overlay
    from saitenka.app.subtitle_modes import TrackPorts
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.subtitle_render import SubtitleTarget


class SessionHost(Protocol):
    """This feature's whole host coupling. See `PanelHost` for why it is spelled out."""

    ov: Overlay
    lifecycle_surfaces: LifecycleSurfaces
    subtitle_pipeline: SubtitleModeCoordinator
    tooltip_controller: TooltipController

    @property
    def track_ports(self) -> TrackPorts: ...

    def translation_wanted(self) -> bool: ...

    def teardown_tip(self) -> None: ...

    def subtitle_target(self) -> SubtitleTarget: ...

    def setup_secondary(self) -> int | None: ...

    def draw_translation(self) -> None: ...


class SessionAdapter:
    def __init__(self, host: SessionHost) -> None:
        self._host = host

    def inputs(self) -> session_intents.SessionInputs:
        host = self._host
        return session_intents.SessionInputs(
            overlay_visible=host.ov.visible,
            translation_wanted=host.translation_wanted(),
        )

    def apply(self, effect: session_intents.SessionEffect, /) -> None:
        host = self._host
        if isinstance(effect, DismissHover):
            host.tooltip_controller.retire_selection()
            host.teardown_tip()
        elif isinstance(effect, session_intents.SetSurfacesVisible):
            host.lifecycle_surfaces.set_visible(visible=effect.visible)
        elif isinstance(effect, session_intents.ReleaseSecondarySubtitles):
            subtitle_modes.release_secondary(host.track_ports)
        elif isinstance(effect, session_intents.SuspendSubtitles):
            host.subtitle_pipeline.suspend_for_overlay(host.subtitle_target())
        elif isinstance(effect, session_intents.ResumeSubtitles):
            host.subtitle_pipeline.resume_after_overlay(host.subtitle_target())
        elif isinstance(effect, session_intents.ShowTranslation):
            host.setup_secondary()
            host.draw_translation()
