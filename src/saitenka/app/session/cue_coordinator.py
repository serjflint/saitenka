"""Named cross-owner transactions for one cue and one episode slot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from saitenka_tokenize.languages import SECOND_LANG

from saitenka import otel_metrics
from saitenka.app import native_subtitles, subnav_settle, subtitle_modes, subtitle_raster
from saitenka.app.features.annotation.annotation_controller import AnnotationInputs
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.runtime import CueCommandState
from saitenka.app.subtitle_geometry_diagnostics import cue_digest
from saitenka.app.subtitle_render import DrawRequest, SubtitleTarget
from saitenka.app.token_cache import cue_key
from saitenka.runtime import events, playback

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.analysis.analysis_controller import AnalysisCommandEndpoint
    from saitenka.app.features.annotation.annotation_controller import (
        AnnotationTransition,
        CueAnnotationController,
    )
    from saitenka.app.features.help.help_controller import ScreenState
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
    from saitenka.app.features.profiles.profile_session import ProfileSession
    from saitenka.app.features.subtitle import SubtitleAcquisitionController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.features.translation import TranslationController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.subtitle_adapter import (
        SubtitleNavigationCoordinator,
        SubtitleTrackCoordinator,
    )
    from saitenka.app.subtitle_ownership import SelectedSid
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.mpvio.osd import Overlay
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CueOwners:
    """The bounded owners participating in cue and episode transactions."""

    ipc: MpvIPC
    overlay: Overlay
    surfaces: LifecycleSurfaces
    screen: ScreenState
    playback: PlaybackObservationController
    presentation: SubtitlePresentation
    annotation: CueAnnotationController
    tooltip: TooltipController
    preview: PreviewCommandEndpoint
    history: HistoryOwner
    tracks: SubtitleTrackStore
    track_commands: SubtitleTrackCoordinator
    navigation: SubtitleNavigationCoordinator
    profile: ProfileSession
    analysis: AnalysisCommandEndpoint
    picker: PickerController
    acquisition: SubtitleAcquisitionController
    translation: TranslationController


class CueCoordinator:
    """Own cue application and the ordering that spans cue and episode owners."""

    def __init__(self, owners: CueOwners) -> None:
        self._o = owners
        self._pending: playback.ObservedCue | None = None
        self._identity_ever_installed = False
        self._settled_hooks: list[Callable[[], None]] = []
        self._settling = False

    def on_settled(self, hook: Callable[[], None]) -> None:
        """Run `hook` after a cue observation has been reconciled — the cue-driven boundary for
        anything that follows the active line (the sidebar), instead of a check on every turn."""
        self._settled_hooks.append(hook)

    @property
    def revision(self) -> int:
        return self._o.playback.state.cue.cue.value

    def command_state(self, *, retired: bool) -> CueCommandState:
        if not retired:
            return CueCommandState.ACTIVE
        if self._identity_ever_installed:
            return CueCommandState.RETIRED_AFTER_ACTIVE
        return CueCommandState.NEVER_INSTALLED

    def mark_identity_installed(self) -> None:
        self._identity_ever_installed = True

    def observe(self, cue: playback.ObservedCue) -> None:
        self._pending = cue

    def settle(self) -> None:
        o = self._o
        cue, self._pending = self._pending, None
        if cue is None:
            otel_metrics.record_cue_settle("no-observation")
            return
        before = o.playback.cue.text
        self._settling = True
        try:
            _track_color_wait(cue.text)
            with otel_metrics.traced(
                "cue_reconcile", cue_revision=str(self.revision), cue=cue_digest(cue.text)
            ) as span:
                o.navigation.reconcile(cue.text)
                settled = "adopted" if o.playback.cue.text != before else "reinstalled"
                otel_metrics.record_cue_settle(settled, span)
        finally:
            self._settling = False
        self._run_settled_hooks()

    def _run_settled_hooks(self) -> None:
        for hook in self._settled_hooks:
            hook()

    def set_subtitle(
        self,
        text: str,
        *,
        revise_session_cue: bool = False,
        provisional_navigation: bool = False,
    ) -> None:
        o = self._o
        if provisional_navigation:
            o.track_commands.navigation.current.nav_provisional_cue_counted = False
        log.debug(
            "sub-text change: %d chars, paused=%s", len(text.strip()), o.playback.value("pause")
        )
        _track_color_wait(text)
        with otel_metrics.instrumented(
            otel_metrics.cue_redraw_duration_ms, "cue_redraw", cue=cue_digest(text)
        ):
            self._set_subtitle_inner(
                text,
                revise_session_cue=revise_session_cue,
                provisional_navigation=provisional_navigation,
            )
        if not self._settling:
            # A cue installed by the session itself — navigation's pre-armed target, a track
            # switch — moves the active line as much as an observed one does. Inside a settle the
            # reconcile's own hook run follows.
            self._run_settled_hooks()

    def _set_subtitle_inner(
        self,
        text: str,
        *,
        revise_session_cue: bool,
        provisional_navigation: bool,
    ) -> None:
        o = self._o
        # Identity, not an unconditional bump: mpv publishes `sub-text` for the blank gap between
        # cues and re-publishes an unchanged line, and each one used to move the fence that every
        # speculation and in-flight render is checked against.
        o.presentation.pipeline.invalidate(
            (
                text,
                o.playback.value("sub-start"),
                o.playback.value("sub-end"),
                o.playback.value("sid"),
            )
        )
        o.presentation.pipeline.cue_changed(o.presentation.target(), nonempty=bool(text.strip()))
        with otel_metrics.traced("teardown_tip"):
            o.tooltip.teardown()
        o.tooltip.retire_selection()
        o.playback.dispatch(events.CueTextReplaced(text))
        self.clear_identity()
        o.track_commands.navigation.current.nav_idx = -1
        with otel_metrics.traced("hide_preview"):
            o.preview.hide()
        if not text.strip():
            o.presentation.cue.reset()
            if o.presentation.native is not None:
                o.presentation.native.mark_empty()
            o.presentation.pipeline.clear(o.surfaces, o.ipc)
            o.presentation.pipeline.flush_focus(o.presentation.target())
            hide = getattr(o.overlay, "hide_interactive", o.overlay.hide)
            hide(OverlayId.TIP)
            return
        self._record_session_cue(
            text,
            revise=revise_session_cue,
            provisional_navigation=provisional_navigation,
        )
        o.presentation.cue.reset()
        transition = o.annotation.replace(text, self.annotation_inputs())
        self.apply_annotation_transition(transition, draw=True)
        o.presentation.pipeline.flush_focus(o.presentation.target())

    def _record_session_cue(self, text: str, *, revise: bool, provisional_navigation: bool) -> None:
        o = self._o
        recorder = o.history.recorder
        if recorder is None:
            return
        identity = (
            o.tracks.current.language,
            o.playback.value("sub-start"),
            o.playback.value("sub-end"),
            text,
        )
        if revise:
            recorder.revise_cue(identity)
            return
        counted = recorder.record_cue(identity)
        if provisional_navigation:
            o.track_commands.navigation.current.nav_provisional_cue_counted = counted

    def annotation_inputs(self) -> AnnotationInputs:
        o = self._o
        dictionaries = o.profile.profile.dict_set
        navigation = o.track_commands.navigation.current
        return AnnotationInputs(
            source_epoch=o.playback.state.media.source.value,
            track_identity=o.playback.value("sid"),
            subtitle_role=o.tracks.current.language,
            observed_start=o.playback.value("sub-start"),
            observed_end=o.playback.value("sub-end"),
            source_order=navigation.nav_idx if navigation.nav_idx >= 0 else None,
            tokenizer=o.profile.profile.tokenizer,
            terms_exist=getattr(dictionaries, "terms_exist", None),
            scorer=o.profile.scorer,
            selected_dictionaries=len(getattr(dictionaries, "dicts", ())),
            dependencies_ready=dictionaries is not None,
            annotate=o.tracks.current.language != SECOND_LANG,
        )

    def apply_annotation_transition(self, transition: AnnotationTransition, *, draw: bool) -> None:
        o = self._o
        if transition.identity is not None:
            o.playback.dispatch(
                events.CueIdentityInstalled(
                    transition.identity.observed_start,
                    transition.identity.observed_end,
                )
            )
            self.mark_identity_installed()
        if transition.cue is not None:
            o.presentation.cue.install_tokenized(transition.cue)
        if transition.schedule_geometry and o.presentation.native is not None:
            o.presentation.cue.replace_geometry(boxes=[])
            o.presentation.native.schedule(self.geometry_observation())
        if draw:
            o.presentation.draw()

    def geometry_observation(self) -> native_subtitles.GeometryObservation:
        o = self._o
        navigation = o.track_commands.navigation.current
        return native_subtitles.GeometryObservation(
            prop=o.playback.value,
            osd=o.screen.osd,
            text=o.playback.cue.text,
            tokens=o.presentation.cue.current.tokens,
            lines=o.presentation.cue.current.lines,
            index=navigation.sub_index,
            normalise=cue_key,
            nav_index=navigation.nav_idx,
            cue_hint=navigation.geometry_cue_hint,
            cue_revision=self.revision,
            is_skippable=o.profile.profile.tokenizer.is_skippable,
        )

    def build_target(
        self,
        pipeline: SubtitleModeCoordinator,
        geometry: native_subtitles.NativeSubtitleGeometry | None,
    ) -> SubtitleTarget:
        o = self._o
        return SubtitleTarget(
            ipc=o.ipc,
            get=o.playback.query,
            prop=o.playback.value,
            surfaces=o.surfaces,
            refresh=_noop if geometry is None else partial(self._refresh_geometry, geometry),
            draw_request=self.draw_request,
            source=None if geometry is None else geometry.source_path,
            native_unsupported=geometry is not None and geometry.source_unsupported,
            legacy_forced=pipeline.legacy_forced,
        )

    def _refresh_geometry(self, geometry: native_subtitles.NativeSubtitleGeometry) -> None:
        geometry.refresh(self.geometry_observation())

    def draw_request(self) -> DrawRequest:
        o = self._o
        view = o.annotation.view
        tooltip = o.tooltip.observation()
        return DrawRequest(
            text=o.playback.cue.text,
            lines=o.presentation.cue.current.lines,
            osd=o.screen.osd,
            sub_size=o.presentation.visual.size(o.screen.osd[1]),
            bg_opacity=o.presentation.visual.background_opacity,
            bottom_margin=o.presentation.visual.bottom_margin(o.screen.osd[1]),
            secondary_role=o.tracks.current.language == SECOND_LANG,
            upgrade_pending=view.pending_text is not None,
            annotation_degraded=view.degraded,
            annotation_visible=subtitle_raster.annotation_visible(
                mode=view.mode,
                hover_annotation=view.hover_revealed,
            ),
            hover=tooltip.selected,
            hover_span=tooltip.metadata.span,
            styles=o.presentation.cue.current.styles,
            boxes=o.presentation.cue.current.boxes,
            owed_color=None
            if o.presentation.native is None
            else o.presentation.native.eligible_tokens,
            paused=bool(o.playback.value("pause")),
        )

    def configure_subtitle_mode(
        self,
        startup: subtitle_modes.SubtitleStartup,
        *,
        slang: str = "ja,jpn,jp",
        second_slang: str = "en",
    ) -> None:
        o = self._o
        subtitle_modes.configure(
            startup,
            slang=slang,
            second_slang=second_slang,
            declare=o.track_commands.declare,
            activate=self._activate_mode,
            secondary_sid=o.playback.query("secondary-sid"),
            ipc=o.ipc,
            invalidate=o.analysis.invalidate,
        )

    def _activate_mode(self, sid: SelectedSid) -> None:
        presentation = self._o.presentation
        presentation.pipeline.activate(presentation.target(), sid, draw=presentation.draw)

    def rebuild_sub_index(self) -> None:
        from saitenka.app.embedded_subs import build_sub_index_for_current_track

        o = self._o
        build_sub_index_for_current_track(
            o.ipc,
            o.playback.query,
            o.navigation.load_index,
            o.presentation.native,
        )

    def clear_identity(self) -> None:
        self._o.annotation.retire_cue()
        self._request_playback_retirement()

    def retire(self, reason: str) -> None:
        o = self._o
        if not o.annotation.retire_cue():
            self._request_playback_retirement()
            return
        log.debug("cue interaction retired: %s", reason)
        self._request_playback_retirement()
        if reason == playback.RetireReason.CUE_TEXT.value and self._mid_seek_transient():
            # Our own sub-seek pre-armed the target cue's surfaces; mpv reports an empty (or the
            # pre-nav) `sub-text` while the seek is in flight. The identity is retired like any
            # other — the reconcile that adopts the landed cue keys off it — but the surfaces
            # stay: resetting them on the blip cost the first frame after every seek its color.
            log.debug("cue surfaces kept across a mid-seek transient")
            return
        o.tooltip.teardown()
        o.tooltip.retire_selection()
        o.presentation.cue.reset()
        if not o.playback.cue.text.strip():
            # The line is gone, and this is the only place its pixels come down: the reconcile
            # treats a blank as already retired, and the geometry refresh — which used to pay this
            # by accident, on an observation it could not key — now waits for one it can.
            if o.presentation.native is not None:
                o.presentation.native.mark_empty()
            o.presentation.pipeline.clear(o.surfaces, o.ipc)

    def _mid_seek_transient(self) -> bool:
        current = self._o.navigation.navigation.current
        return subnav_settle.swallows(
            current.sub_settle,
            text=self._o.playback.cue.text,
            nav_prev_text=current.nav_prev_text,
            identity_reinstall=False,
        )

    def replace_source(self, path: object = None, *, reason: str) -> None:
        o = self._o
        o.navigation.retire_settle()
        o.playback.dispatch(events.SourceReplaced(path))
        self.retire(reason)

    def rebind_episode(self) -> None:
        o = self._o
        o.picker.close()
        o.acquisition.retire_episode()
        o.annotation.retire_episode_warm()
        o.translation.retire_episode()
        o.playback.retire_episode()
        if not o.playback.routed:
            o.tracks.dispatch(events.EpisodeRetired())
            o.tooltip.retire_episode()
        o.track_commands.navigation.replace()
        o.presentation.cue.reset()
        o.annotation.retire_cue()

    def _request_playback_retirement(self) -> None:
        self._o.playback.dispatch(events.CueIdentityRetireRequested(playback.RetireReason.CUE_TEXT))


def _track_color_wait(text: str) -> None:
    """Open (or keep) the color wait for *text*, or end it when the line owes no color.

    A blank cue is the gap between lines, not a line waiting to be colored — starting a clock there
    would leave one running until the next cue displaced it, and every such gap would then report
    the following cue's wait as longer than it was.
    """
    if text.strip():
        otel_metrics.record_cue_arrival(cue_digest(text))
    else:
        otel_metrics.forget_color_wait()


def _noop() -> None:
    pass
