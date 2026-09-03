"""Cross-feature consequences of replacing the active reading profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka_tokenize.languages import SECOND_LANG

from saitenka.app import subtitle_modes

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.analysis.analysis_controller import AnalysisCommandEndpoint
    from saitenka.app.features.annotation.annotation_controller import (
        AnnotationInputs,
        AnnotationTransition,
        CueAnnotationController,
    )
    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.features.subtitle.navigation_state import NavigationStore
    from saitenka.app.features.tooltip.preparation import TooltipPreparationController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.app.subtitle_selection import SubtitleStartup
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore


@dataclass(frozen=True, slots=True)
class ProfileIntegration:
    """Apply one profile generation to the owners whose derived facts depend on it."""

    ipc: MpvIPC
    profile: ProfileController
    annotation: CueAnnotationController
    analysis: AnalysisCommandEndpoint
    preparation: TooltipPreparationController
    tooltip: TooltipController
    presentation: SubtitlePresentation
    tracks: SubtitleTrackStore
    navigation: NavigationStore
    cue_text: Callable[[], str]
    annotation_inputs: Callable[[], AnnotationInputs]
    apply_annotation: Callable[[AnnotationTransition], None]
    teardown_tooltip: Callable[[], None]
    retire_cue: Callable[[str], None]
    configure_subtitle_mode: Callable[[SubtitleStartup, str, str], None]
    rebuild_index: Callable[[], None]
    track_ports: Callable[[], subtitle_modes.TrackPorts]

    def enable_async_annotation(self) -> None:
        self.annotation.enable_async()

    def dependencies_changed(self) -> None:
        self.analysis.invalidate(vocabulary_changed=True)
        self.invalidate_dictionary()
        transition = self.annotation.dependencies_changed(
            self.cue_text(),
            self.annotation_inputs(),
        )
        if transition is None:
            return
        self.teardown_tooltip()
        self.tooltip.retire_selection()
        self.presentation.cue.reset()
        if self.presentation.native is not None:
            self.presentation.native.invalidate(live=True)
        self.apply_annotation(transition)

    def warm_episode(self) -> None:
        index = self.navigation.current.sub_index
        if index is None or not self.preparation.config.enabled or self.profile.dict_set is None:
            return
        self.annotation.start_episode_warm(index, self.annotation_inputs())

    def invalidate_tokenizer(self) -> None:
        if self.presentation.native is not None:
            self.presentation.native.invalidate(live=True)
        else:
            self.presentation.pipeline.invalidate()
        self.annotation.invalidate_tokenizer()

    def invalidate_dictionary(self) -> None:
        self.preparation.invalidate_dependencies(self.tooltip)

    def reset_episode_warm(self) -> None:
        self.annotation.retire_episode_warm()

    def has_subtitle_track(self, slang: str) -> bool:
        return subtitle_modes.has_track_for_slang(self.ipc, slang)

    def select_subtitle_track(self, new_slang: str, second_slang: str) -> None:
        current = self.tracks.current
        preferred_role = current.language if current.slang == new_slang else None
        startup = subtitle_modes.select_initial(
            self.ipc,
            new_slang,
            second_slang,
            preferred_role=preferred_role,
        )
        self.configure_subtitle_mode(startup, new_slang, second_slang)
        ports = self.track_ports()
        if ports.translation_visible():
            subtitle_modes.setup_secondary(ports)
        self.navigation.current.sub_index = None
        self.rebuild_index()

    def select_translation_track(self, slang: str, second_slang: str) -> None:
        subtitle_modes.select_translation(self.track_ports(), slang, second_slang)

    def retokenize_current_cue(self) -> None:
        text = self.cue_text()
        if not text.strip() or self.tracks.current.language == SECOND_LANG:
            return
        if self.annotation.view.async_enabled:
            self.retire_cue("profile")
        transition = self.annotation.retokenize(text, self.annotation_inputs())
        if transition is not None:
            self.apply_annotation(transition)
