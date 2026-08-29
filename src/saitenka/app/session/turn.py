"""Owner-thread session turn and the feature application graph it settles.

Bounded collaborators own feature state and policy. This module applies their outcomes on the mpv
owner thread while the public live-session state machine stays in :mod:`saitenka.app.session.controller`.
"""

from __future__ import annotations

import logging
import time
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app.capabilities import CapabilityProbe
    from saitenka.app.features.analysis.analysis_controller import (
        AnalysisCommandEndpoint,
        AnalysisController,
        AnalysisObservation,
    )
    from saitenka.app.features.annotation import jobs as cue_annotation
    from saitenka.app.features.annotation.annotation_controller import CueAnnotationController
    from saitenka.app.features.help.help_controller import HelpController, ScreenState
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.picker import sub_picker
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.preview.preview_controller import PreviewController
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
    from saitenka.app.session.assembly import SessionAssembly
    from saitenka.app.session.command_runtime import CommandRuntime
    from saitenka.app.session.cue_coordinator import CueCoordinator
    from saitenka.app.session.interaction_adapter import (
        InteractionCoordinator,
    )
    from saitenka.app.session.lifecycle import SessionLifecycle
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.session.runtime import SessionRuntime
    from saitenka.app.session.stateless import StatelessCommandGraph
    from saitenka.app.subtitle_adapter import (
        SubtitleNavigationCoordinator,
        SubtitleTrackCoordinator,
    )
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.app.toast_controller import ToastController
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.mpvio.osd import Overlay
    from saitenka.runtime.connection import ConnectionStore
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore

from saitenka import otel_metrics
from saitenka.app import (
    backlog,
    episode_reslot,
    logsetup,
    native_subtitles,
    session_stats,
    subtitle_modes,
    subtitle_raster,
)
from saitenka.app.features.annotation.annotation_controller import (
    AnnotationInputs,
    AnnotationTransition,
)
from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
from saitenka.app.features.tooltip.popups import (
    PopupView,
)
from saitenka.app.languages import SECOND_LANG
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.subtitle_render import (
    DrawRequest,
    SubtitleTarget,
)
from saitenka.app.token_cache import cue_key
from saitenka.runtime import (
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    StartupReady,
    UserCommand,
    events,
    playback,
)

log = logging.getLogger(__name__)
#: For the two lines the user is meant to read on the terminal (logsetup.CONSOLE_LOGGER_NAME).
console_log = logsetup.user_facing_logger()

# Local names for the shared OSD slot registry (overlay_ids.OverlayId is the single source of truth
# so extracted subsystems can't collide on slot numbers). IntEnum → drop-in int at every call site.
SUB_ID = OverlayId.SUB
TIP_ID = OverlayId.TIP
NESTED_ID = OverlayId.NESTED
# The nested popup gets its own (roomier) height cap (TooltipOptions.nested_max_frac) so shrinking
# the base tooltip (tip_max_frac) doesn't cramp the deep-dive.


# Every mpv size/scale source, probed at each osd-dimensions change to diagnose why the tooltip scale
# (osd_h/REF_H) jitters: which source is stable (a candidate to key scale off) vs which wobbles. Unknown
# props return None (mpv errors → data None) — harmless. video-out-params is a dict (dw/dh/w/h/aspect).
_DISPLAY_PROBE_PROPS = (
    "osd-width",
    "osd-height",
    "osd-par",
    "dwidth",
    "dheight",
    "width",
    "height",
    "window-scale",
    "current-window-scale",
    "display-hidpi-scale",
    "display-fps",
    "display-names",
    "fullscreen",
    "window-maximized",
    "window-minimized",
    "focused",
    "video-out-params",
)


def _discard(_value: object) -> None:
    pass


# Popup view/panel classes live in the tooltip feature; the _Nested alias is kept because the controller
# internals and the test-suite reference the old private name.
_Nested = PopupView


class SessionTurn:
    """Admit and settle one owner-thread turn over the resolved feature graph."""

    _assembly: SessionAssembly
    ipc: MpvIPC
    _interactive_ready: bool
    _connection: ConnectionStore
    ov: Overlay
    lifecycle_surfaces: LifecycleSurfaces
    screen: ScreenState
    help_controller: HelpController
    analysis_controller: AnalysisController
    annotation_controller: CueAnnotationController
    picker_controller: PickerController
    sidebar_controller: SidebarController
    preview_controller: PreviewController
    tooltip_preparation: TooltipPreparationController
    interaction_surfaces: InteractionSurfaces
    lifecycle_timers: LifecycleTimers
    notifications: ToastController
    subtitle_presentation: SubtitlePresentation
    _capability_submit: JobSubmitter | None
    _tts_capability: CapabilityProbe | None
    history: HistoryOwner
    tooltip_controller: TooltipController
    _subtitle_tracks: SubtitleTrackStore
    _mouse_in: bool
    _scrolled_this_tick: bool
    playback_observation: PlaybackObservationController
    mining_controller: MiningController
    profile_session: ProfileSession
    analysis_observation: AnalysisObservation
    analysis_commands: AnalysisCommandEndpoint
    subtitle_acquisition: SubtitleAcquisitionController
    _mouse: MouseCapture
    translation_observation: TranslationObservation
    track_commands: SubtitleTrackCoordinator
    translation_controller: TranslationController
    subtitle_navigation: SubtitleNavigationCoordinator
    profile_integration: ProfileIntegration
    _nudge_pending: bool
    interaction: InteractionCoordinator
    _stateless_commands: StatelessCommandGraph
    _cue: CueCoordinator
    episode_watch: episode_reslot.EpisodeWatch
    command_runtime: CommandRuntime
    _lifecycle: SessionLifecycle
    entry_runtime: SessionRuntime

    def __init__(self) -> None:
        raise TypeError("SessionTurn is assembled by create_session_controller")

    @property
    def lifecycle(self) -> SessionLifecycle:
        return self._lifecycle

    def rebuild_sub_index(self) -> None:
        """Re-index whichever track mpv has selected. The one place the four facts are bound."""
        from saitenka.app.embedded_subs import build_sub_index_for_current_track

        build_sub_index_for_current_track(
            self.ipc,
            self.playback_observation.query,
            self.subtitle_navigation.load_index,
            self.subtitle_presentation.native,
        )

    @property
    def _playback(self) -> playback.PlaybackState:
        return self.playback_observation.state

    def _geometry_observation(self) -> native_subtitles.GeometryObservation:
        """The facts the geometry owner decides from, per operation — they all move per cue."""
        return native_subtitles.GeometryObservation(
            prop=self.playback_observation.value,
            osd=self.screen.osd,
            text=self.playback_observation.cue.text,
            tokens=self.subtitle_presentation.cue.current.tokens,
            lines=self.subtitle_presentation.cue.current.lines,
            index=self.track_commands.navigation.current.sub_index,
            normalise=cue_key,
            nav_index=self.track_commands.navigation.current.nav_idx,
            cue_hint=self.track_commands.navigation.current.geometry_cue_hint,
            cue_revision=self.cue_revision,
            is_skippable=self.profile_session.profile.tokenizer.is_skippable,
        )

    def _build_subtitle_target(
        self,
        pipeline: SubtitleModeCoordinator,
        geometry: native_subtitles.NativeSubtitleGeometry | None,
    ) -> SubtitleTarget:
        return SubtitleTarget(
            ipc=self.ipc,
            get=self.playback_observation.query,
            prop=self.playback_observation.value,
            surfaces=self.lifecycle_surfaces,
            refresh=(
                (lambda: None)
                if geometry is None
                else (lambda: geometry.refresh(self._geometry_observation()))
            ),
            draw_request=self._draw_request,
            source=None if geometry is None else geometry.source_path,
            native_unsupported=geometry is not None and geometry.source_unsupported,
            legacy_forced=pipeline.legacy_forced,
        )

    def _draw_request(self) -> DrawRequest:
        """Snapshot the host once per draw, so the values cannot drift apart mid-render.

        The ONE place in the draw path that reads the host; everything downstream of it is a value.
        Named rather than inlined at its callers: two copies of this snapshot that drift apart is
        precisely the bug `DrawRequest` was introduced to prevent.
        """
        return DrawRequest(
            text=self.playback_observation.cue.text,
            lines=self.subtitle_presentation.cue.current.lines,
            osd=self.screen.osd,
            sub_size=self.subtitle_presentation.visual.size(self.screen.osd[1]),
            bg_opacity=self.subtitle_presentation.visual.background_opacity,
            bottom_margin=self.subtitle_presentation.visual.bottom_margin(self.screen.osd[1]),
            secondary_role=self._subtitle_tracks.current.language == SECOND_LANG,
            upgrade_pending=self.annotation_controller.view.pending_text is not None,
            annotation_degraded=self.annotation_controller.view.degraded,
            annotation_visible=subtitle_raster.annotation_visible(
                mode=self.annotation_controller.view.mode,
                hover_annotation=self.annotation_controller.view.hover_revealed,
            ),
            hover=self.tooltip_controller.observation().selected,
            hover_span=self.tooltip_controller.observation().metadata.span,
            styles=self.subtitle_presentation.cue.current.styles,
            boxes=self.subtitle_presentation.cue.current.boxes,
            paused=bool(self.playback_observation.value("pause")),
        )

    def _publish_cue_identity(self, identity: cue_annotation.CueIdentity) -> None:
        """Project the annotation owner's identity into playback conflict detection."""
        self.playback_observation.dispatch(
            events.CueIdentityInstalled(identity.observed_start, identity.observed_end)
        )
        self._cue.mark_identity_installed()

    def refresh_osd(self) -> bool:
        d = self.playback_observation.value("osd-dimensions") or {}
        w, h = int(d.get("w") or self.screen.osd[0]), int(d.get("h") or self.screen.osd[1])
        if (w, h) != self.screen.osd and w > 0 and h > 0:
            self.screen.osd = (w, h)
            if self.subtitle_presentation.native is None:
                self.subtitle_presentation.pipeline.invalidate()
            self._probe_display_sources("osd-change", d)
            return True
        return False

    def _probe_display_sources(self, reason: str, osd: dict) -> None:
        """Snapshot EVERY mpv size/scale source at an osd-dimensions change, so a report pinpoints WHICH
        one makes the tooltip scale (osd_h/REF_H) jitter — e.g. on retina the OSD backing-pixel height
        wobbles a few px while ``display-hidpi-scale`` stays a clean 2.0 (→ key scale off the stable one).
        Emits a low-cardinality ``osd_probe`` span (trace_report breaks down each source's distinct values)
        + a full-fidelity log line. Cheap: only fires on an actual osd change (minutes apart in practice)."""
        probe = {p: self.playback_observation.query(p) for p in _DISPLAY_PROBE_PROPS}
        vop = probe.get("video-out-params")
        vop = vop if isinstance(vop, dict) else {}
        span_attrs = {
            "reason": reason,
            "tip_scale": f"{self.tooltip_controller.scale().display:.4f}",
            "osd_w": str(osd.get("w")),
            "osd_h": str(osd.get("h")),
            "osd_mt": str(osd.get("mt")),
            "osd_mb": str(osd.get("mb")),
            "hidpi_scale": str(probe.get("display-hidpi-scale")),
            "window_scale": str(probe.get("current-window-scale") or probe.get("window-scale")),
            "dwidth": str(probe.get("dwidth")),
            "dheight": str(probe.get("dheight")),
            "vop_dh": str(vop.get("dh")),
            "fullscreen": str(probe.get("fullscreen")),
        }
        with otel_metrics.traced("osd_probe", **span_attrs):
            pass
        log.info(
            "display sources (%s): tip_scale=%s osd=%r probe=%r",
            reason,
            span_attrs["tip_scale"],
            osd,
            probe,
        )

    def set_subtitle(
        self,
        text: str,
        *,
        revise_session_cue: bool = False,
        provisional_navigation: bool = False,
    ) -> None:
        if provisional_navigation:
            self.track_commands.navigation.current.nav_provisional_cue_counted = False
        # Per-cue breadcrumb (low frequency): correlates mpv's sub-text change with the overlay draw +
        # paused-state in the report — the mpv-log-vs-overlay-log gap the paused-OSD bug lives in.
        log.debug(
            "sub-text change: %d chars, paused=%s",
            len(text.strip()),
            self.playback_observation.value("pause"),
        )
        # Seek-to-paint chain: this span covers everything below (teardown/tokenize/score/render/
        # upload) for one cue. Nests as a child of sub_nav's "sub_seek" span for the instant-nav
        # (Alt+←/→/↓) path, or of "sub_text_reconcile" for an mpv-driven change (native sub-seek /
        # normal cue advance) — either way, its duration IS the "seek command → drawn" latency.
        with otel_metrics.instrumented(otel_metrics.cue_redraw_duration_ms, "cue_redraw"):
            self._set_subtitle_inner(
                text,
                revise_session_cue=revise_session_cue,
                provisional_navigation=provisional_navigation,
            )

    def _set_subtitle_inner(
        self,
        text: str,
        *,
        revise_session_cue: bool = False,
        provisional_navigation: bool = False,
    ) -> None:
        self.subtitle_presentation.pipeline.invalidate()
        self.subtitle_presentation.pipeline.cue_changed(
            self.subtitle_presentation.target(), nonempty=bool(text.strip())
        )
        # Tear down the hover stack via the shared path BEFORE mutating sub_text/hover so that
        # TIP_ID/NESTED_ID are hidden, _tip_rect/_tip_state/_tip_key/_nest are reset, and any
        # any tooltip-owned pause is released. `retire_hover` will not do: it early-returns on
        # hover already -1, which does not imply the tip is down (`_show_tooltip` can be called
        # without a hover).
        with otel_metrics.traced("teardown_tip"):
            self.tooltip_controller.teardown()
        self.tooltip_controller.retire_selection()
        self.playback_observation.dispatch(events.CueTextReplaced(text))
        self._cue.clear_identity()
        self.track_commands.navigation.current.nav_idx = (
            -1
        )  # any external cause of a cue change invalidates the nav chaining hint
        with otel_metrics.traced("hide_preview"):
            self.preview_commands.hide()
        if not text.strip():
            self.subtitle_presentation.cue.reset()
            if self.subtitle_presentation.native is not None:
                self.subtitle_presentation.native.mark_empty()
            self.subtitle_presentation.pipeline.clear(self.lifecycle_surfaces, self.ipc)
            hide = getattr(self.ov, "hide_interactive", self.ov.hide)
            hide(TIP_ID)
            return
        self._record_session_cue(
            text,
            revise=revise_session_cue,
            provisional_navigation=provisional_navigation,
        )
        self.subtitle_presentation.cue.reset()
        transition = self.annotation_controller.replace(text, self._annotation_inputs())
        self._apply_annotation_transition(transition, draw=True)

    def _record_session_cue(self, text: str, *, revise: bool, provisional_navigation: bool) -> None:
        recorder = self.history.recorder
        if recorder is None:
            return
        identity = (
            self._subtitle_tracks.current.language,
            self.playback_observation.value("sub-start"),
            self.playback_observation.value("sub-end"),
            text,
        )
        if revise:
            recorder.revise_cue(identity)
            return
        counted = recorder.record_cue(identity)
        if provisional_navigation:
            self.track_commands.navigation.current.nav_provisional_cue_counted = counted

    def prepare_subtitle_blocking(self, text: str) -> None:
        """Prepare a demo/screenshot cue through the annotation worker before capture."""
        self.annotation_controller.prepare_blocking(
            text,
            self._annotation_inputs(),
            drive=self._drive_annotation_once,
        )
        self.set_subtitle(text)

    def _drive_annotation_once(self, timeout: float | None) -> None:
        """A turn taken from inside cue construction, so it settles nothing: the reconcile this is
        nested in owns the batch boundary, and running a second one here would build the cue again
        against the half-updated identity the outer one is still assembling."""
        self.ipc.receive_session(timeout, self._drain_event)

    def _annotation_inputs(self) -> AnnotationInputs:
        dictionaries = self.profile_session.profile.dict_set
        return AnnotationInputs(
            source_epoch=self._playback.media.source.value,
            track_identity=self.playback_observation.value("sid"),
            subtitle_role=self._subtitle_tracks.current.language,
            observed_start=self.playback_observation.value("sub-start"),
            observed_end=self.playback_observation.value("sub-end"),
            source_order=self.track_commands.navigation.current.nav_idx
            if self.track_commands.navigation.current.nav_idx >= 0
            else None,
            tokenizer=self.profile_session.profile.tokenizer,
            terms_exist=getattr(dictionaries, "terms_exist", None),
            scorer=self.profile_session.scorer,
            selected_dictionaries=len(getattr(dictionaries, "dicts", ())),
            dependencies_ready=dictionaries is not None,
            annotate=self._subtitle_tracks.current.language != SECOND_LANG,
        )

    def _apply_annotation_transition(self, transition: AnnotationTransition, *, draw: bool) -> None:
        if transition.identity is not None:
            self._publish_cue_identity(transition.identity)
        if transition.cue is not None:
            self.subtitle_presentation.cue.install_tokenized(transition.cue)
        if transition.schedule_geometry and self.subtitle_presentation.native is not None:
            self.subtitle_presentation.cue.replace_geometry(boxes=[])
            self.subtitle_presentation.native.schedule(self._geometry_observation())
        if draw:
            self.subtitle_presentation.draw()

    @cached_property
    def preview_commands(self) -> PreviewCommandEndpoint:
        return PreviewCommandEndpoint(
            preview=self.preview_controller,
            help=self.help_controller,
            tip_keys_bound=lambda: self.tooltip_controller.keybindings_bound,
            mining=self.mining_controller,
            surfaces=self.lifecycle_surfaces,
            screen=self.screen,
            ipc=self.ipc,
            keys=self._assembly.keys,
            tip_scale_override=self.tooltip_controller.visual.scale_override,
            tip_max_frac=self.tooltip_controller.visual.base_height_fraction,
            play_audio=self.mining_controller.play_audio,
            cue=self.subtitle_presentation.cue,
            playback=self.playback_observation,
            toast=self.toast,
        )

    @property
    def listing_ports(self) -> sub_picker.ListingPorts:
        """Compatibility projection of the picker-owned listing capability."""
        return self.picker_controller.listing_ports(
            navigation=self.track_commands.navigation,
            stop=self._lifecycle.stop_signal,
            toast=self.toast,
        )

    @property
    def capture_ports(self) -> backlog.CapturePorts:
        """What a bookmark toggle samples the cue from — read now, so the write is this cue."""
        return backlog.CapturePorts(
            video=self.playback_observation.text("path"),
            start=self.playback_observation.number("sub-start"),
            end=self.playback_observation.number("sub-end"),
            text=self.playback_observation.cue.text,
            secondary_text=self.translation_observation.secondary_text(),
            language=self._subtitle_tracks.current.language,
            tokens=self.subtitle_presentation.cue.current.tokens,
            hover=self.tooltip_controller.observation().selected,
            jp_sid=self._subtitle_tracks.current.jp_sid,
            en_sid=self._subtitle_tracks.current.en_sid,
            tracks=self.playback_observation.sequence("track-list"),
            store=self.history.ensure_backlog,
            toast=self.toast,
            record_capture=self.history.record_capture,
        )

    @property
    def reslot_ports(self) -> episode_reslot.ReslotPorts:
        """What re-slotting the overlay onto a newly loaded episode does."""
        return episode_reslot.ReslotPorts(
            ipc=self.ipc,
            finish_stats=lambda: self.history.finish(self.analysis_controller.result),
            start_stats=lambda: self.history.start(
                path=lambda: self.playback_observation.value("path"),
                arm=self.arm_session_persist,
            ),
            rebind_episode=self._cue.rebind_episode,
            rebuild_index=self.rebuild_sub_index,
            configure_mode=self.configure_subtitle_mode,
            configure_retry=self.subtitle_acquisition.configure_retry,
            configure_picker=self.picker_controller.configure_listing,
            fetch_japanese=self.subtitle_acquisition.fetch_background,
            start_prefetch=self.tooltip_controller.start_prefetch,
            toast=self.toast,
        )

    @property
    def watch_ports(self) -> episode_reslot.WatchPorts:
        """What wiring the follow-mpv-onto-the-next-episode hooks needs."""
        return self.episode_watch.ports()

    def _telemetry_gauges(self) -> dict[str, float]:
        """Live cache-size gauges for the telemetry interval sampler (writer thread, ~1s cadence — NOT
        the hot path). ``panel_cache.bytes`` is the retained (compressed) on-heap footprint;
        ``dict_cache.size`` the decoded-entry count across every dictionary. The tooltip owner reads
        its cache under the lock it owns, so a concurrent prefetch mutation cannot fault iteration."""
        panel_n, panel_bytes = self.tooltip_controller.cache_totals()
        dict_n = (
            self.profile_session.profile.dict_set.decoded_entry_count()
            if self.profile_session.profile.dict_set is not None
            else 0
        )
        gauges = {
            "panel_cache.size": float(panel_n),
            "panel_cache.bytes": float(panel_bytes),
            "dict_cache.size": float(dict_n),
        }
        if self.subtitle_presentation.native is not None:
            stats = self.subtitle_presentation.native.worker.stats
            gauges.update(
                {
                    "subtitle_geometry.submitted": float(stats.submitted),
                    "subtitle_geometry.superseded": float(stats.superseded),
                    "subtitle_geometry.completed": float(stats.completed),
                    "subtitle_geometry.cache_hits": float(stats.cache_hits),
                    "subtitle_geometry.failures": float(stats.failures),
                    "subtitle_geometry.ready_before_presented": float(stats.ready_before_presented),
                    "subtitle_geometry.presented": float(stats.presented),
                    "subtitle_geometry.max_submit_us": float(stats.max_submit_us),
                    "subtitle_geometry.prefetched": float(stats.prefetched),
                    "subtitle_geometry.prefetch_dropped": float(stats.prefetch_dropped),
                    "subtitle_geometry.result_cache_entries": float(stats.result_cache_entries),
                    "subtitle_geometry.prefetch_cache_entries": float(stats.prefetch_cache_entries),
                }
            )
        return gauges

    @property
    def _mouse_captured(self) -> bool:
        return self._mouse.held

    @property
    def _mouse_section_defined(self) -> bool:
        return self._mouse.defined

    def configure_subtitle_mode(
        self, startup: subtitle_modes.SubtitleStartup, *, slang: str = "ja,jpn,jp"
    ) -> None:
        subtitle_modes.configure(
            startup,
            slang=slang,
            declare=self.track_commands.declare,
            activate=lambda sid: self.subtitle_presentation.pipeline.activate(
                self.subtitle_presentation.target(), sid, draw=self.subtitle_presentation.draw
            ),
            secondary_sid=self.playback_observation.query("secondary-sid"),
            ipc=self.ipc,
            invalidate=self.analysis_commands.invalidate,
        )

    def toast(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        self.notifications.show(text, kind, seconds)

    def _open_picker_command(self) -> None:
        self.picker_controller.open(
            self.playback_observation.query("path"),
            retire_hover=self.tooltip_controller.retire_hover,
            navigation=self.track_commands.navigation,
            stop=self._lifecycle.stop_signal,
            toast=self.toast,
        )

    def _redraw_after_resize(self) -> None:
        """Re-lay everything the window size decides, after an `osd-dimensions` change.

        Was `_refresh_surfaces`, a tick stage that re-detected per tick the change the projection
        already publishes. The name went with the mechanism: this runs because a resize was
        observed, not because a tick came round.
        """
        if self.refresh_osd():
            if self.playback_observation.cue.text.strip():
                self.subtitle_presentation.draw()
            self.help_controller.redraw()
            self.analysis_controller.redraw()
            # row capacity changed, so the active row may need re-centring
            self.sidebar_controller.follow()

    def _apply_capabilities(self) -> None:
        if self._tts_capability is not None:
            self._tts_capability.request()
        self.mining_controller.refresh_capability()

    # --- run loop -----------------------------------------------------------------------------
    def pump(self, timeout: float | None = 0.0) -> bool:
        """Consume one turn of events, blocking up to ``timeout``. False if mpv went away.

        Not a tick. Nothing here runs *because time passed* — the turn exists because events
        arrived, and with no events and no timeout this does nothing at all. The stages that used
        to run every 40th of a second each moved to the delta or deadline that actually drives
        them, which is what left this as a drain and a pair of post-drain settlements.
        """
        try:
            self._scrolled_this_tick = False  # set by scroll_tip below (wheel or TIP_UP/DOWN)
            # Sampled before the drain: cue reconciliation draws from the batch boundary, so a
            # sample taken after it would miss the very draw the paused nudge exists to re-flush.
            ops_before = self.ov.ops
            self._drain_events(timeout)
            if not self._connection.current.ready:
                return True
            self._schedule_paused_nudge(ops_before)
            if self._mark_interactive_ready():
                # A settlement published a session fact *after* this turn's drain, so the runtime
                # would not see it until the next one — and "the first completed turn clears the
                # startup hint" would quietly become "the second one does". Readiness latches, so
                # this second drain happens exactly once per session, not once per turn.
                self._drain_events(0.0)
            return True
        except (OSError, ValueError):
            return False

    def _flush_paused_nudge(self) -> None:
        """Poke the throttled OSD so mpv actually presents a draw that landed while paused.

        Reached from the nudge deadline, not from the next tick. The deadline's revision fence is
        what coalesces a burst of draws into one repaint, so nothing here has to track whether one
        is already owed.
        """
        self._nudge_pending = False
        self.lifecycle_surfaces.repaint()
        # No per-nudge log line — the osd_paused_nudge counter below carries the count (this fired
        # ~1600×/session at debug, 67% of the log, duplicating the counter for zero added detail).
        if otel_metrics.osd_paused_nudge is not None:
            otel_metrics.osd_paused_nudge.add(1)

    def _drain_events(self, timeout: float | None = 0.0) -> None:
        # Reset rather than rebuilt, which is the same thing and leaves the guard reachable: a
        # file-load has to break the coalescing window, and with the boundary an effect now, the
        # performer is what breaks it. The window itself stays drain-local — coalescing across two
        # drains would suppress a genuine second press, and a batch is a property of arrival.
        self.ipc.receive_session(timeout, self._drain_event)
        self._cue.settle()

    @property
    def cue_revision(self) -> int:
        """The projection's cue revision — the identity a geometry refresh was armed for."""
        return self._playback.cue.cue.value

    def _drain_event(self, ev: object) -> None:
        # The three connection arms are the no-reactor fallback, and nothing else: a session with
        # one claims all three, reduces them in the SESSION slice and performs these same acts as
        # registered effects. Every migrated lifecycle duty keeps a path like this — a screenshot
        # capture and most unit tests are sessions that never had a runtime.
        if isinstance(ev, ConnectionLost):
            self._connection.observed(ev)
            self._cue.retire("connection-lost")
            return
        if isinstance(ev, ConnectionReplaced):
            self._on_ipc_reconnect()
            return
        if isinstance(ev, events.FileLoaded):
            self.episode_watch.file_loaded()
            return
        if isinstance(ev, events.PropertyObserved):
            self.playback_observation.observe(ev.name, ev.data)
            return
        if isinstance(ev, ConnectionReady):
            # Only reached without a reactor: a session that has one claims this, because learning
            # the transport is back is the whole of what the event means. Its twin cannot be
            # claimed — losing the transport also strands a cue identity.
            self._connection.observed(ev)
            return
        if isinstance(ev, UserCommand):
            self.command_runtime.perform(ev)
            return
        if not isinstance(ev, dict):
            log.debug("ignored unsupported runtime event: %s", type(ev).__name__)
            return
        kind = ev.get("event")
        # The wire shape, for a session with no gateway: nothing has named the event, so the dict
        # is all there is. Three layers, one writer — a reactor performs the effect, a gateway
        # without one hands over `FileLoaded`, and this is what is left when neither exists.
        if kind == "file-loaded":
            self.episode_watch.file_loaded()
        elif kind == "property-change":
            self.playback_observation.observe_event(ev)
        elif kind == "client-message":
            args = ev.get("args") or [""]
            name = args[0] if isinstance(args[0], str) else ""
            self.command_runtime.handle(UserCommand(name, tuple(args[1:])))

    def arm_capability_refresh(self, seconds: float = 0.5) -> None:
        """Keep asking whether the optional services have come up, on a deadline of its own.

        The probes are TTL-gated, so the tick's 25 ms cadence was almost entirely no-op calls into
        a lock. Half a second is far below any TTL and costs nothing; what matters is that a
        service appearing mid-session is still noticed without a tick to notice it.

        The read stays on the session turn rather than being pushed from the probe's terminal: a
        probe without the runtime lane falls back to its own thread, and letting that thread run
        the mining owner could mutate its seed state from off the turn.
        """

        def due() -> None:
            self._apply_capabilities()
            self.arm_capability_refresh(seconds)

        self.lifecycle_timers.schedule(LifecycleTimerKind.CAPABILITY_REFRESH, seconds, due)

    def arm_session_persist(self, seconds: float) -> None:
        """Keep an uninterrupted session durable.

        Watch time accrues at transitions, and a viewer who never pauses produces none — so without
        this a long session would hold everything in memory until close and lose it all to a crash.
        The due event re-arms, because durability is a standing obligation rather than one deadline.
        """

        def due() -> None:
            session_stats.accrue(
                self.history.recorder,
                paused=bool(self.playback_observation.value("pause")),
                language=self._subtitle_tracks.current.language,
            )
            self.arm_session_persist(seconds)

        self.lifecycle_timers.schedule(LifecycleTimerKind.SESSION_PERSIST, seconds, due)

    def _mark_interactive_ready(self) -> bool:
        """Announce interactive readiness once. True when this call published the fact."""
        if self._interactive_ready:
            return False
        if self.playback_observation.observing and self._playback.value("osd-dimensions") in (
            None,
            {},
        ):
            return False
        self._interactive_ready = True
        connected_at = self.ipc.connected_at  # None until the transport has connected once
        with otel_metrics.traced(
            "startup.interactive_ready",
            cue_pending=str(self.annotation_controller.view.pending_text is not None).lower(),
            deps_pending=str(not self.annotation_controller.view.dependencies_settled).lower(),
        ) as span:
            if connected_at is not None:
                span.set("since_ipc_ms", round((time.monotonic() - connected_at) * 1_000, 3))
            self.ipc.publish_runtime_event(StartupReady())
        return True

    def _schedule_paused_nudge(self, ops_before: int) -> None:
        """An overlay changed while mpv is paused → schedule a re-flush next tick so mpv actually
        presents it (mpv #8172; see Overlay.repaint). Only when paused: playing frames present on
        their own, and re-adding every tick would be wasteful."""
        if self.ov.ops == ops_before or not self.playback_observation.value("pause"):
            return

        # Fails open: with no timer port the repaint runs inline. A nudge that never fires is a
        # frozen overlay the user has to jiggle the mouse to unstick — mpv #8172 in full — which is
        # far worse than one that fires a moment early.
        self._nudge_pending = self.lifecycle_timers.schedule(
            LifecycleTimerKind.PAUSED_REPAINT, 0.0, self._flush_paused_nudge
        )
        if not self._nudge_pending:
            self._flush_paused_nudge()
        if otel_metrics.osd_paused_draw is not None:
            otel_metrics.osd_paused_draw.add(1)

    def _check_startup_health(self) -> None:
        """One-time startup diagnostic for 'mpv plays but the overlay can't draw'. The RELIABLE failure
        signal is a dead read direction, NOT missing subtitles: a section can legitimately have no subs
        for minutes (an anime OP), so 'no sub-text' alone must never warn — that was the old
        false-alarm. We WARN only when mpv's replies aren't reaching us — zero bytes ever read (the
        classic Windows named-pipe failure) or osd-dimensions never resolved — because then nothing can
        draw regardless of subtitles. If the pipe is alive but there's simply no cue yet, note it once
        at debug. Lives in overlay.log / report; playback is unaffected."""
        secs = 8.0
        bytes_read = self.ipc._bytes_read  # the read counter has no public reader
        osd_ok = self.playback_observation.value("osd-dimensions") not in (None, {})
        if bytes_read == 0 or not osd_ok:
            log.warning(
                "IPC looks dead %.0fs after start (bytes from mpv=%d, osd-dimensions=%s) — mpv's "
                "replies/events aren't reaching the overlay, so nothing will draw (Windows named-pipe "
                "read failure, or attached to a not-yet-ready mpv).",
                secs,
                bytes_read,
                "ok" if osd_ok else "None",
            )
        elif not self.playback_observation.cue.text:
            log.debug(
                "IPC alive %.0fs in (bytes=%d, osd-dimensions ok) but no subtitle text yet — normal if "
                "this section has no subs (e.g. an OP); the overlay will draw when a cue appears.",
                secs,
                bytes_read,
            )

    def _on_ipc_reconnect(self) -> None:
        self.subtitle_presentation.pipeline.connection_replaced(self.subtitle_presentation.target())
