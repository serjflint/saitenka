"""Validated collaborators for one fully composed study session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.capabilities import CapabilityProbe
    from saitenka.app.episode_reslot import EpisodeWatch, ReslotPorts, WatchPorts
    from saitenka.app.features.analysis.analysis_controller import (
        AnalysisCommandEndpoint,
        AnalysisController,
        AnalysisObservation,
    )
    from saitenka.app.features.annotation.annotation_controller import CueAnnotationController
    from saitenka.app.features.help.help_controller import HelpController, ScreenState
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
    from saitenka.app.features.profiles.profile_integration import ProfileIntegration
    from saitenka.app.features.profiles.profile_session import ProfileSession
    from saitenka.app.features.sidebar.sidebar_controller import SidebarController
    from saitenka.app.features.subtitle import SubtitleAcquisitionController
    from saitenka.app.features.tooltip.preparation import TooltipPreparationController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.features.translation import TranslationController, TranslationObservation
    from saitenka.app.interaction.mouse_capture import MouseCapture
    from saitenka.app.interaction.presentation import InteractionSurfaces
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.session.command_runtime import CommandRuntime
    from saitenka.app.session.cue_coordinator import CueCoordinator
    from saitenka.app.session.interaction_adapter import InteractionCoordinator
    from saitenka.app.session.lifecycle import SessionLifecycle
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.session.stateless import StatelessCommandGraph
    from saitenka.app.session.support import (
        PickerCommandEndpoint,
        SessionDiagnostics,
        SessionPresentation,
        SessionRecurrence,
    )
    from saitenka.app.subtitle_adapter import (
        SubtitleNavigationCoordinator,
        SubtitleTrackCoordinator,
    )
    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.app.toast_controller import ToastController
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.mpvio.osd import Overlay
    from saitenka.runtime.connection import ConnectionStore
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore


@dataclass(frozen=True, slots=True)
class SessionGraph:
    """Complete session graph. Construction publishes no partially bound value."""

    ipc: MpvIPC
    connection: ConnectionStore
    overlay: Overlay
    lifecycle_surfaces: LifecycleSurfaces
    interaction_surfaces: InteractionSurfaces
    screen: ScreenState
    help: HelpController
    analysis: AnalysisController
    annotation: CueAnnotationController
    picker: PickerController
    sidebar: SidebarController
    preview: PreviewController
    tooltip_preparation: TooltipPreparationController
    timers: LifecycleTimers
    notifications: ToastController
    subtitle_presentation: SubtitlePresentation
    capability_submit: JobSubmitter | None
    tts_capability: CapabilityProbe | None
    history: HistoryOwner
    tooltip: TooltipController
    subtitle_tracks: SubtitleTrackStore
    playback: PlaybackObservationController
    mining: MiningController
    profile: ProfileSession
    analysis_observation: AnalysisObservation
    analysis_commands: AnalysisCommandEndpoint
    subtitle_acquisition: SubtitleAcquisitionController
    mouse: MouseCapture
    translation_observation: TranslationObservation
    track_commands: SubtitleTrackCoordinator
    translation: TranslationController
    subtitle_navigation: SubtitleNavigationCoordinator
    profile_integration: ProfileIntegration
    interaction: InteractionCoordinator
    stateless_commands: StatelessCommandGraph
    cue: CueCoordinator
    episode_watch: EpisodeWatch
    commands: CommandRuntime
    lifecycle: SessionLifecycle
    presentation: SessionPresentation
    recurrence: SessionRecurrence
    diagnostics: SessionDiagnostics
    picker_commands: PickerCommandEndpoint
    preview_commands: PreviewCommandEndpoint
    reslot: ReslotPorts
    watch: WatchPorts
