"""Owner-thread session turn and the feature application graph it settles.

Bounded collaborators own feature state and policy. This module applies their outcomes on the mpv
owner thread while the public live-session state machine stays in :mod:`saitenka.app.session.controller`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.config import MiningOptions, ReaderOptions
    from saitenka.app.features.annotation import jobs as cue_annotation
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Scorer
    from saitenka.app.session.assembly import SessionAssembly
    from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer

import saitenka.app.features.sidebar.sidebar as sidebar_module
import saitenka.app.session.resources as session_resources
import saitenka.app.session.runtime as session_runtime
from saitenka import otel_metrics
from saitenka.app import (
    backlog,
    episode_reslot,
    logsetup,
    native_subtitles,
    session_stats,
    subtitle_intents,
    subtitle_modes,
    subtitle_raster,
)
from saitenka.app.bindings import LEGACY_RENDERER_MSG
from saitenka.app.capabilities import CapabilityProbe, configure_runtime_jobs
from saitenka.app.features.analysis.analysis_controller import AnalysisObservation
from saitenka.app.features.annotation.annotation_controller import (
    AnnotationInputs,
    AnnotationTransition,
)
from saitenka.app.features.mining import mine_intents
from saitenka.app.features.mining.mine_adapter import (
    BookmarkCommandEndpoint,
    MineCommandCoordinator,
    MineCommandPorts,
)
from saitenka.app.features.mining.mining_controller import (
    MiningController,
    MiningIdentity,
    MiningSessionAssembly,
)
from saitenka.app.features.mining.mining_encounter import MiningEncounterSource
from saitenka.app.features.mining.mining_projection import MiningProjection
from saitenka.app.features.picker import sub_picker
from saitenka.app.features.preview.preview_endpoint import PreviewCommandEndpoint
from saitenka.app.features.profiles.profile_adapter import ProfileCommandEndpoint
from saitenka.app.features.profiles.profile_controller import (
    ProfileAftermath,
    ProfileController,
    ProfileInvalidation,
    ProfileSubtitles,
)
from saitenka.app.features.profiles.profile_integration import ProfileIntegration
from saitenka.app.features.profiles.profile_session import (
    ProfileDependencyPorts,
    ProfileSession,
    ProfileSessionAssembly,
)
from saitenka.app.features.sidebar.sidebar_controller import SidebarViewOwners
from saitenka.app.features.subtitle import SubtitleAcquisitionController
from saitenka.app.features.subtitle.navigation_state import NavigationStore
from saitenka.app.features.tooltip import (
    tooltip_controller,
)
from saitenka.app.features.tooltip.hover_adapter import (
    HoverCommandCoordinator,
    HoverCommandPorts,
)
from saitenka.app.features.tooltip.popups import (
    PopupView,
)
from saitenka.app.features.tooltip.tooltip_controller import TooltipSessionContext
from saitenka.app.features.translation import TranslationController, TranslationObservation
from saitenka.app.interaction import mouse_capture
from saitenka.app.languages import SECOND_LANG
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.media import (
    tts_available,
)
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.session import sidebar_coordination, surfaces
from saitenka.app.session.adapter import SessionCommandCoordinator, SessionCommandPorts
from saitenka.app.session.command_runtime import CommandRuntime, CommandRuntimePorts
from saitenka.app.session.cue_coordinator import CueCoordinator, CueTransactions
from saitenka.app.session.interaction_adapter import (
    InteractionCommandCoordinator,
    InteractionCoordinator,
    InteractionPorts,
)
from saitenka.app.session.lifecycle import (
    SessionLifecycle,
    SessionLifecycleActs,
    SessionLifecycleOwners,
    compose_session_lifecycle,
)
from saitenka.app.session.panel_adapter import PanelCommandCoordinator, PanelCommandPorts
from saitenka.app.session.playback_observation import (
    PlaybackObservationController,
    PlaybackStartup,
)
from saitenka.app.session.routes import (
    COMMAND_PERFORMER,
    CUE_RETIRE_RESOURCE,
    PLAYBACK_DELTAS_PERFORMER,
    RESLOT_PARTICIPANT,
    STATELESS_COMMANDS,
    SUBTITLE_REPLAY_PARTICIPANT,
    stateless_features,
)
from saitenka.app.session.runtime import SessionRuntime
from saitenka.app.session.stateless import StatelessCommandGraph
from saitenka.app.subtitle_adapter import (
    SubtitleCommandApply,
    SubtitleCommandCoordinator,
    SubtitleCommandRead,
    SubtitleNavigationCoordinator,
    SubtitleTrackCoordinator,
)
from saitenka.app.subtitle_presentation import (
    SubtitlePresentation,
    SubtitlePresentationPorts,
    SubtitleVisualSettings,
)
from saitenka.app.subtitle_render import (
    DrawRequest,
    SubtitleTarget,
)
from saitenka.app.token_cache import cue_key
from saitenka.runtime import (
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    Owner,
    StartupReady,
    UserCommand,
    events,
    playback,
)
from saitenka.runtime.connection import ConnectionStore
from saitenka.runtime.effects import ApplyPlaybackDeltas
from saitenka.runtime.hover import HoverDelays
from saitenka.runtime.presentation_slice import TranslationStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.subtitles import GeometryBackend

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

    def __init__(  # noqa: PLR0913 -- resolved graph conversion is completed below
        self,
        ipc: MpvIPC,
        assembly: SessionAssembly,
        options: ReaderOptions,
        *,
        scorer: Scorer | None = None,
        anki=None,
        mine_cfg=None,
        dict_set=None,
        renderer: SubtitleRenderer | NullRenderer | None = None,
        geometry_backend: GeometryBackend | None = None,
        profile: Profile | None = None,
        tts_ok: bool | None = None,
        tooltip_runtime_jobs: Callable[
            [tooltip_controller.TooltipRuntimeJobs],
            tooltip_controller.TooltipRuntimeJobs,
        ]
        | None = None,
    ):
        """Install an already-resolved session assembly without interpreting compatibility inputs."""
        o = options
        self._assembly = assembly
        self.ipc = ipc
        registrations: list[tuple[str, object]] = []
        self.subtitle_navigation: SubtitleNavigationCoordinator
        self._cue: CueCoordinator
        self._interactive_ready = False
        self._connection = ConnectionStore(ipc)
        # Supplied by composition (`create_session_controller`), never probed off `ipc`: which egress the
        # overlay uses is a wiring decision, not something to infer from a collaborator's methods.
        self.ov = assembly.overlay
        self.lifecycle_surfaces = assembly.surfaces
        self.screen = assembly.screen
        self.help_controller = assembly.help
        self.analysis_controller = assembly.analysis
        self.annotation_controller = assembly.annotation
        self.picker_controller = assembly.picker
        self.sidebar_controller = assembly.sidebar
        self.preview_controller = assembly.preview
        self.tooltip_preparation = assembly.tooltip_preparation
        # Hand teardown to the runtime at the point of construction, so the lifetime belongs to
        # whoever owns it rather than to a line in a teardown table far away. We keep *using* it;
        # what moves is when it closes. False means no runtime owns this session, and the close
        # table's fallback still has to run.
        # `getattr`, like the job-lane port below: a partial IPC (the benches' fake) constructs a
        # SessionController without implementing every runtime port, and construction must not demand one.

        self.interaction_surfaces = assembly.interaction_surfaces
        self.lifecycle_timers = assembly.timers
        self.notifications = assembly.notifications
        stop = assembly.stop

        def clear_subtitle_interaction() -> None:
            self.tooltip_controller.teardown()
            self.tooltip_controller.retire_selection()

        self.subtitle_presentation = SubtitlePresentation(
            ipc,
            settings=o.subtitle_geometry,
            visual=SubtitleVisualSettings.from_options(o.tooltip),
            renderer=renderer,
            backend=geometry_backend,
            ports=SubtitlePresentationPorts(
                target=self._build_subtitle_target,
                geometry=self._geometry_observation,
                clear_interaction=clear_subtitle_interaction,
                redraw_cue=lambda: self.set_subtitle(self.playback_observation.cue.text),
                tokenize_lookahead=self.annotation_controller.captured_lookahead(
                    self._annotation_inputs
                ),
            ),
        )
        # Progressive startup: deps loaded on a background thread, injected on the main thread by the
        # poll loop (see load_deps_async / _apply_deps). Until then, subs render plain + a spinner shows.
        initial_profile_name = profile.name if profile is not None else "default"
        mining_identity = MiningIdentity(initial_profile_name, 0)
        # Interactive sessions publish this optional subprocess probe later; deterministic
        # demo/screenshot assembly supplies it synchronously through SessionServices.
        self._capability_submit = configure_runtime_jobs(ipc)
        self._tts_capability = (
            None
            if tts_ok is not None
            else CapabilityProbe(
                tts_available,
                name="tts",
                ttl=3_600.0,
                retry=60.0,
                submit=self._capability_submit,
            )
        )
        tooltip_visual = tooltip_controller.TooltipVisualSettings.from_options(o.tooltip)
        self.history = assembly.history
        log.info(
            "layout backend: %s (requested %r)",
            tooltip_visual.backend_name,
            o.tooltip.layout_engine,
        )
        self.tooltip_controller = tooltip_controller.TooltipController(
            ipc,
            self.tooltip_preparation,
            self.screen,
            self.lifecycle_timers,
            assembly.keys,
            self.help_controller,
            config=tooltip_controller.TooltipControllerConfig(
                panel_cache_max=o.tooltip.panel_cache_max,
                pause_enabled=o.tooltip.pause_on_tooltip,
                delays=HoverDelays(
                    scan=o.tooltip.scan_delay,
                    hide=o.tooltip.hide_delay,
                    switch=o.tooltip.hover_switch_delay,
                ),
                flash_seconds=o.tooltip.flash_secs,
                key_context=assembly.tooltip_keys,
                visual=tooltip_visual,
            ),
            runtime_jobs=tooltip_runtime_jobs,
        )
        self._subtitle_tracks = assembly.subtitle_tracks
        profile_integration: ProfileIntegration
        profile_controller = ProfileController(
            profile,
            dict_set,
            ProfileInvalidation(
                invalidate_tokenizer=lambda: profile_integration.invalidate_tokenizer(),
                invalidate_dictionary=lambda: profile_integration.invalidate_dictionary(),
                reset_episode_warm=lambda: profile_integration.reset_episode_warm(),
            ),
            ProfileSubtitles(
                current_subtitle_slang=lambda: self._subtitle_tracks.current.slang,
                has_subtitle_track=lambda slang: profile_integration.has_subtitle_track(slang),
                select_subtitle_track=lambda slang: profile_integration.select_subtitle_track(
                    slang
                ),
                retokenize_current_cue=lambda: profile_integration.retokenize_current_cue(),
            ),
            ProfileAftermath(
                warm_episode=lambda: profile_integration.warm_episode(),
                notify=lambda text, kind: self.toast(text, kind),
            ),
        )
        self._mouse_in = False  # cursor over the video window — an engagement signal
        self._scrolled_this_tick = False  # a wheel/tip-scroll ran this poll tick — for render-span
        # attribution (did hover-driven scan/nested-popup work land in the same tick as a scroll?)
        self.playback_observation = PlaybackObservationController(
            self.ipc,
            self._apply_playback_delta,
            PlaybackStartup(
                reconcile_cue=lambda text: self.subtitle_navigation.reconcile(text),
                refresh_render_space=self.refresh_osd,
                observe_authored_subtitle=lambda reply: (
                    self.subtitle_presentation.native.observe_ass_full_reply(reply)
                    if self.subtitle_presentation.native is not None
                    else None
                ),
                probe_display_sources=self._probe_display_sources,
            ),
        )
        self.mining_controller = self._assemble_mining_controller(
            mining_identity,
            anki,
            mine_cfg,
            assembly.mining,
            profile_controller,
            stop,
        )
        navigation = NavigationStore()
        self.profile_session = ProfileSession(
            ProfileSessionAssembly(
                profile_controller,
                self.mining_controller,
                self.lifecycle_timers,
                self.lifecycle_surfaces,
                ProfileDependencyPorts(
                    enable_async_annotation=lambda: profile_integration.enable_async_annotation(),
                    dependencies_changed=lambda: profile_integration.dependencies_changed(),
                    start_prefetch=self.tooltip_controller.start_prefetch,
                    warm_episode=lambda: profile_integration.warm_episode(),
                ),
                lambda: self.tooltip_preparation.worker_count,
                lambda mode, workers: console_log.info(
                    "runtime: %s · %d prefetch worker(s)", mode, workers
                ),
            ),
            identity=mining_identity,
            scorer=scorer,
        )
        self.analysis_observation = AnalysisObservation(
            self._subtitle_tracks,
            navigation,
            self.profile_session,
        )
        self.analysis_commands = self.analysis_controller.endpoint(
            self.analysis_observation.current
        )
        # The subtitle raster, retired at `RENDERING`. `native_geometry` is installed after this
        # point, so every one of these resolves it when it closes rather than now.
        # The two connection acts. Registered here with the rest and late-bound for the same
        # reason: both read collaborators this constructor has not finished building.
        registrations.append(
            (
                SUBTITLE_REPLAY_PARTICIPANT,
                # Late-bound like every other registered step: an early-bound method also freezes the
                # seam a test replaces, and these two are reached only through the effect.
                session_resources.Starting(lambda: self._on_ipc_reconnect()),
            )
        )
        registrations.append(
            (RESLOT_PARTICIPANT, session_resources.Starting(lambda: self._on_file_loaded()))
        )
        registrations.append(
            (
                PLAYBACK_DELTAS_PERFORMER,
                session_resources.Performing(lambda effect: self._apply_playback_deltas(effect)),
            )
        )
        # `Owner.SUBTITLE`'s slice: which mpv track plays which role. Session-lived like the
        # playback one, and episode-safe because a re-slot always runs `configure_subtitle_mode`,
        # whose event resets the whole state.
        self.subtitle_acquisition = self._assemble_subtitle_acquisition(
            assembly.subtitle_fetch,
            stop,
        )
        surface_router = surfaces.build_surface_router(
            self.help_controller,
            self.picker_controller,
            self.sidebar_controller,
            self.preview_controller,
            self.tooltip_controller,
        )
        self._mouse = mouse_capture.MouseCapture(
            ipc,
            self.lifecycle_timers,
            surface_router.wants_mouse_capture,
        )
        self.translation_observation = TranslationObservation(
            self.ov,
            self.tooltip_controller,
            self.playback_observation,
            self.screen,
        )
        translation_controller: TranslationController
        self.track_commands = SubtitleTrackCoordinator(
            ipc=self.ipc,
            tracks=self._subtitle_tracks,
            navigation=navigation,
            playback=self.playback_observation,
            property_value=self.playback_observation.query,
            notifications=self.notifications,
            invalidate=self.analysis_commands.invalidate,
            translation_visible=lambda: translation_controller.active(
                self.translation_observation.current()
            ),
            rebuild_index=self.rebuild_sub_index,
            install_cue=self.set_subtitle,
        )
        translation_store = TranslationStore(self.ipc)
        translation_controller = TranslationController(
            translation_store,
            self.lifecycle_surfaces,
            self.track_commands,
            auto_reveal=o.translation.auto_translate,
        )
        self.translation_controller = translation_controller
        self.sidebar_controller.bind_view(
            SidebarViewOwners(
                tracks=self.track_commands,
                playback=self.playback_observation,
                screen=self.screen,
                surfaces=self.lifecycle_surfaces,
                history=self.history,
                mining=self.mining_controller,
                profile=self.profile_session,
                analysis=self.analysis_controller,
                timers=self.lifecycle_timers,
            )
        )
        self.subtitle_navigation = SubtitleNavigationCoordinator(
            ipc=self.ipc,
            navigation=self.track_commands.navigation,
            geometry=lambda: self.subtitle_presentation.native,
            get=self.playback_observation.query,
            cue_text=lambda: self.playback_observation.cue.text,
            cue_retired=lambda: self.annotation_controller.view.retired,
            draw_cue=self.set_subtitle,
            replace_source=lambda path=None, *, reason: self._cue.replace_source(
                path, reason=reason
            ),
            invalidate=self.analysis_commands.invalidate,
            warm_tokens=lambda: profile_integration.warm_episode(),
            index_changed=self.sidebar_controller.index_changed,
            cue_revision=lambda: self.cue_revision,
            invalidate_pipeline=self.subtitle_presentation.pipeline.invalidate,
        )
        profile_integration = ProfileIntegration(
            ipc=self.ipc,
            profile=self.profile_session.profile,
            annotation=self.annotation_controller,
            analysis=self.analysis_commands,
            preparation=self.tooltip_preparation,
            tooltip=self.tooltip_controller,
            presentation=self.subtitle_presentation,
            tracks=self._subtitle_tracks,
            navigation=self.track_commands.navigation,
            cue_text=lambda: self.playback_observation.cue.text,
            annotation_inputs=self._annotation_inputs,
            apply_annotation=lambda transition: self._apply_annotation_transition(
                transition, draw=True
            ),
            teardown_tooltip=self.tooltip_controller.teardown,
            retire_cue=lambda reason: self._cue.retire(reason),
            configure_subtitle_mode=lambda startup, slang: self.configure_subtitle_mode(
                startup, slang=slang
            ),
            rebuild_index=self.rebuild_sub_index,
        )
        self.profile_integration = profile_integration

        stateless_commands: StatelessCommandGraph

        def run_stateless(command: object) -> None:
            stateless_commands.run(command)

        tts_capability = self._tts_capability

        def tts_is_available() -> bool:
            return bool(tts_ok) if tts_capability is None else bool(tts_capability.value)

        def hide_tooltip() -> None:
            self.interaction_surfaces.remove(OverlayId.TIP)

        self.tooltip_controller.bind_session_context(
            TooltipSessionContext(
                hide_tooltip=hide_tooltip,
                surfaces=self.interaction_surfaces,
                screen=self.screen,
                preparation=self.tooltip_preparation,
                annotation=self.annotation_controller,
                presentation=self.subtitle_presentation,
                profile=self.profile_session,
                mining=self.mining_controller,
                playback=self.playback_observation,
                translation=self.translation_controller,
                translation_observation=self.translation_observation,
                history=self.history,
                notifications=self.notifications,
                tracks=self._subtitle_tracks,
                navigation=self.track_commands.navigation,
                preview_click=self.preview_commands.click,
                run_hover_command=run_stateless,
                run_mine_command=run_stateless,
                tts_available=tts_is_available,
            )
        )
        self._ass_full_probe_dirty = True
        # #100 auto-advance: run mode installs a re-slot callback; the presence of the hook IS the
        # opt-in (never set under attach, so SyncPlay-managed playback never advances). The
        # eof-reached edge is one-shot per file because a delta only exists when the value changed.
        self.advance_hook: Callable[[], bool] | None = None
        # #100 reactive re-slot: `reslot_hook` fires on EVERY mpv `file-loaded` (our own eof loadfile,
        # a native autoload/playlist advance, a manual next/prev) so the overlay follows whatever mpv
        # plays — installed for any interactive run, independent of auto-advance. `_slotted_path` dedups
        # the file we've already set up (the initial load, or a redundant file-loaded for the same file).
        self.reslot_hook: Callable[[Path], None] | None = None
        self._slotted_path: Path | None = None
        self.screen.osd = (1280, 720)
        # Normalized source of a cue drawn PLAIN because its annotation can't complete yet (dicts
        # loading); reader_deps re-renders it annotated once deps land. None = drawn annotated.
        self._nudge_pending = (
            False  # a draw happened while paused → re-flush the OSD next tick (#8172)
        )

        def settle_annotation() -> None:
            for transition in self.annotation_controller.settle():
                self._apply_annotation_transition(transition, draw=transition.publish)

        def build_sidebar_actions() -> sidebar_module.SidebarActions:
            return sidebar_module.SidebarActions(
                seek=lambda name, at: send_correlated(
                    ipc,
                    name,
                    "set_property",
                    "time-pos",
                    at,
                    owner=Owner.PLAYBACK,
                ),
                bookmark=lambda: run_stateless(mine_intents.MineCommand.BOOKMARK_CUE),
                mine=lambda: run_stateless(mine_intents.MineCommand.WORD),
                open_mined=lambda note_id: sidebar_coordination.open_mined(
                    self.sidebar_controller.view(),
                    build_sidebar_actions(),
                    self.preview_commands.ports(),
                    self.preview_commands.card_source(),
                    note_id,
                ),
            )

        def download_ports() -> sub_picker.DownloadPorts:
            return sub_picker.DownloadPorts(
                self.toast,
                self.subtitle_acquisition.submit,
                self.playback_observation.query,
                self.lifecycle_surfaces,
            )

        self.interaction = InteractionCoordinator(
            InteractionPorts(
                overlay_visible=lambda: bool(getattr(self.ov, "visible", True)),
                playback=self.playback_observation,
                router=surface_router,
                tooltip=self.tooltip_controller,
                sidebar=self.sidebar_controller,
                download=download_ports,
                sidebar_actions=build_sidebar_actions,
                hide_annotation=lambda: self.tooltip_controller.set_annotation_hover(
                    revealed=False
                ),
                settle_annotation=settle_annotation,
                sync_mouse_capture=self._mouse.sync,
            )
        )
        stateless_commands = self._assemble_stateless_commands(
            self.interaction.command_coordinator()
        )
        self._stateless_commands = stateless_commands
        self._cue = CueCoordinator(
            CueTransactions(
                settle_interaction=self.interaction.settle,
                current_text=lambda: self.playback_observation.cue.text,
                reconcile_text=self.subtitle_navigation.reconcile,
                revision=lambda: self.cue_revision,
                reduce_playback=self.playback_observation.dispatch,
                retire_settle_window=self.subtitle_navigation.retire_settle,
                retire_annotation_cue=self.annotation_controller.retire_cue,
                teardown_tooltip=self.tooltip_controller.teardown,
                retire_tooltip_selection=self.tooltip_controller.retire_selection,
                reset_cue_render=self.subtitle_presentation.cue.reset,
                close_picker=self.picker_controller.close,
                retire_acquisition_episode=self.subtitle_acquisition.retire_episode,
                retire_annotation_warm=self.annotation_controller.retire_episode_warm,
                retire_translation_episode=self.translation_controller.retire_episode,
                playback_routed=lambda: self.playback_observation.routed,
                retire_playback_episode=self.playback_observation.retire_episode,
                retire_subtitle_episode=lambda: _discard(
                    self._subtitle_tracks.dispatch(events.EpisodeRetired())
                ),
                retire_tooltip_episode=self.tooltip_controller.retire_episode,
                replace_navigation=self.track_commands.navigation.replace,
            )
        )
        registrations.append(
            (
                CUE_RETIRE_RESOURCE,
                session_resources.Retiring(lambda: self._cue.retire("connection-lost")),
            )
        )

        self.command_runtime = CommandRuntime(
            CommandRuntimePorts(
                ipc=self.ipc,
                keys=self._assembly.keys,
                contributed_handlers=self._assembly.command_handlers(),
                contributed_specs=self._assembly.command_specs(),
                stateless=stateless_commands,
                toggle_renderer=self.subtitle_presentation.toggle_renderer,
                mining=self.mining_controller,
                connection=self._connection,
                cue=self._cue,
                annotation=self.annotation_controller,
                help=self.help_controller,
                mouse=self._mouse,
            ),
            legacy_renderer_message=LEGACY_RENDERER_MSG,
        )
        registrations.append(
            (COMMAND_PERFORMER, session_resources.Performing(self.command_runtime.run_effect))
        )

        self._lifecycle = compose_session_lifecycle(
            SessionLifecycleOwners(
                ipc,
                self._tts_capability,
                self.mining_controller,
                self.tooltip_controller,
                self.tooltip_preparation,
                self.annotation_controller,
                self.analysis_controller,
                self._mouse,
                self.subtitle_presentation.refresh,
                self.subtitle_presentation,
                self.history,
                self.lifecycle_timers,
                self.lifecycle_surfaces,
                self.ov,
            ),
            SessionLifecycleActs(
                render_space=self.refresh_osd,
                start_observing=self.playback_observation.start_session,
                install_input=self.command_runtime.install_input,
                arm_capabilities=self.arm_capability_refresh,
                start_prefetch=self.tooltip_controller.start_prefetch,
                finish_mask_atlas=self.tooltip_preparation.finish_mask_atlas,
                history_path=lambda: self.playback_observation.value("path"),
                arm_history=self.arm_session_persist,
                telemetry_gauges=self._telemetry_gauges,
                startup_health=self._check_startup_health,
                retire_settle_window=self.subtitle_navigation.retire_settle,
                finish_history=lambda: self.history.finish(self.analysis_controller.result),
                report_history=self.history.report,
            ),
            registrations=registrations,
            stop=stop,
        )
        facts = session_runtime.SessionFacts(
            refresh_osd=self.refresh_osd,
            prop=self.playback_observation.value,
            get=self.playback_observation.query,
            tokens=lambda: self.subtitle_presentation.cue.current.tokens,
            is_content_token=lambda token: self.profile_session.profile.tokenizer.is_content(token),
            osd_height=lambda: self.screen.osd[1],
            painted=lambda: (
                self.lifecycle_surfaces.settled() and self.interaction_surfaces.settled()
            ),
        )
        acts = session_runtime.SessionActs(
            drive_annotation_once=self._drive_annotation_once,
            prepare_subtitle=self.prepare_subtitle_blocking,
            prepare_hover=self.tooltip_controller.prepare_hover_blocking,
            mark_ready=self._mark_interactive_ready,
            scroll_tip=self.tooltip_controller.scroll_tip,
            toggle_translation=self._stateless_commands.handler(
                subtitle_intents.SubtitleCommand.TOGGLE_TRANSLATION
            ),
            mine_current=self._stateless_commands.handler(mine_intents.MineCommand.WORD),
            bulk_mine=self._stateless_commands.handler(mine_intents.MineCommand.EPISODE),
        )
        self.entry_runtime = SessionRuntime(facts, acts, self.ipc)

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

    def _apply_playback_deltas(self, effect: object) -> None:
        """Perform `ApplyPlaybackDeltas`: `Owner.PLAYBACK`'s outbox, delivered.

        The tuple is bound by the effect rather than read back off the slice, which is also what
        makes it safe to re-enter: applying one delta can reduce another event (`AuthoredCueStale`
        probes mpv and seeds the reply) and replace the slice underneath this loop.
        """
        assert isinstance(effect, ApplyPlaybackDeltas)
        for delta in effect.deltas:
            self._apply_playback_delta(delta)

    def _probe_ass_full(self) -> None:
        """Resolve mpv's authored-ASS capability once per file. Driven by `AuthoredCueStale`, which
        the projection publishes on the same observation that invalidated the cached probe."""
        if self.subtitle_presentation.native is None or not self._ass_full_probe_dirty:
            return
        if self.subtitle_presentation.native.ass_full_capability.value == "unknown":
            reply = self.ipc.probe("sub-text/ass-full")
            self.playback_observation.dispatch(
                events.PropertySeeded("sub-text/ass-full", reply.get("data"))
            )
            self.subtitle_presentation.native.observe_ass_full_reply(reply)
        self._ass_full_probe_dirty = False

    def _apply_playback_delta(self, delta: playback.PlaybackDelta) -> None:
        if isinstance(delta, playback.CueIdentityRetired):
            self._cue.retire(delta.reason.value)
        elif isinstance(delta, playback.AuthoredCueStale):
            self._probe_ass_full()
        elif isinstance(delta, playback.CueObservationChanged):
            self._cue.observe(delta.cue)
        elif isinstance(delta, playback.SubtitleSelectionChanged):
            self.subtitle_presentation.refresh.retire()  # the track it was armed for is gone
            if self.subtitle_presentation.native is not None:
                self.subtitle_presentation.native.set_source(None, live=True)
            else:
                self.subtitle_presentation.pipeline.invalidate()
            subtitle_modes.on_primary_changed(self.track_commands.ports(), delta.sid)
        elif isinstance(delta, playback.SubtitleTimingChanged):
            if self.subtitle_presentation.native is not None:
                self.subtitle_presentation.native.record_clock_change(
                    self.playback_observation.value
                )
        elif (
            isinstance(delta, playback.GeometryInputChanged)
            and self.subtitle_presentation.native is not None
        ):
            self.subtitle_presentation.refresh.arm()
        else:
            self._apply_session_delta(delta)

    def _apply_session_delta(self, delta: playback.PlaybackDelta) -> None:
        """Deltas whose consumer is the session rather than the cue pipeline — split off its
        sibling for the complexity ratchet, and they do read as a group."""
        if isinstance(delta, playback.RenderSpaceChanged):
            # Only the window size: the rest of the render space is sub-rendering options, which
            # change the geometry a cue is laid out in without resizing anything to redraw.
            if delta.property_name == "osd-dimensions":
                self._redraw_after_resize()
        elif isinstance(delta, playback.EndOfFileChanged):
            # #100: on the rising edge, ask the installed hook to re-slot to the next episode. No
            # seen-it-already latch — mpv sits paused at EOF republishing the same value, and the
            # projection's unchanged-value guard already turns that into silence. A hook that
            # returns False (no sibling, ambiguous) is a no-op; mpv holds the last frame.
            if delta.reached and self.advance_hook is not None:
                self.advance_hook()
        elif isinstance(delta, playback.PauseChanged):
            log.debug("mpv pause -> %s", delta.paused)
            # Watch time is accrued at the transition, not sampled by a tick: the segment that
            # just ended is exactly what the change delimits, and an idle runtime does no work.
            session_stats.accrue(
                self.history.recorder,
                paused=bool(self.playback_observation.value("pause")),
                language=self._subtitle_tracks.current.language,
            )
        elif isinstance(delta, playback.SecondaryTextChanged):
            inputs = self.translation_observation.current()
            self.translation_controller.secondary_text_changed(
                replace(inputs, secondary_text=delta.value)
            )
        elif isinstance(delta, playback.PointerMoved):
            # Hover reacts to the pointer moving, not to a tick noticing that it did. The dwells it
            # arms are deadlines, so a cursor that stops still gets its linger — which is why this
            # could not move until they were.
            self.interaction.update_hover()

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
        return episode_reslot.WatchPorts(
            install_reslot_hook=self.install_reslot_hook,
            set_advance_hook=lambda hook: setattr(self, "advance_hook", hook),
            prop=self.playback_observation.value,
            current_media_path=self.current_media_path,
        )

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

    def _assemble_stateless_commands(
        self,
        interaction: InteractionCommandCoordinator,
    ) -> StatelessCommandGraph:
        """Build the closed synchronous command graph from bounded authorities."""
        hover = HoverCommandCoordinator(
            HoverCommandPorts(
                profile=self.profile_session.profile,
                tooltip=self.tooltip_controller,
                cue=self.subtitle_presentation.cue,
                copy_token=self.tooltip_controller.copy_token,
                open_kanji=self.tooltip_controller.open_kanji,
                resume_playback=self.tooltip_controller.resume_after_hover_pause,
                notifications=self.notifications,
            )
        )
        mine = MineCommandCoordinator(
            MineCommandPorts(
                mining=self.mining_controller,
                bookmark=BookmarkCommandEndpoint(
                    playback=self.playback_observation,
                    cue=self.subtitle_presentation.cue,
                    tracks=self._subtitle_tracks,
                    tooltip=self.tooltip_controller,
                    store=self.history.ensure_backlog,
                    property_value=self.playback_observation.query,
                    number_property=self.playback_observation.number,
                    sequence_property=self.playback_observation.sequence,
                    secondary_text=self.translation_observation.secondary_text,
                    notifications=self.notifications,
                    record_capture=self.history.record_capture,
                ),
                notifications=self.notifications,
            )
        )
        panel = PanelCommandCoordinator(
            PanelCommandPorts(
                analysis=self.analysis_commands,
                surfaces=self.lifecycle_surfaces,
                sidebar=self.sidebar_controller,
                picker=self.picker_controller,
                preview=self.preview_commands,
                retire_hover=self.tooltip_controller.retire_hover,
                show_sidebar=self.sidebar_controller.show,
                hide_sidebar=self.sidebar_controller.hide,
                open_picker=self._open_picker_command,
            )
        )
        session = SessionCommandCoordinator(
            SessionCommandPorts(
                overlay=self.ov,
                surfaces=self.lifecycle_surfaces,
                subtitle_pipeline=self.subtitle_presentation.pipeline,
                tooltip=self.tooltip_controller,
                translation=self.translation_controller,
                translation_inputs=self.translation_observation.current,
                teardown_tip=self.tooltip_controller.teardown,
                subtitle_target=self.subtitle_presentation.target,
            )
        )
        subtitle = SubtitleCommandCoordinator(
            SubtitleCommandRead(
                ipc=self.ipc,
                navigation=self.track_commands.navigation,
                playback=self.playback_observation,
                tracks=self._subtitle_tracks,
                cue=self.subtitle_presentation.cue,
                annotation=self.annotation_controller,
                observed_property=self.playback_observation.value,
                property_value=self.playback_observation.query,
                text_property=self.playback_observation.text,
            ),
            SubtitleCommandApply(
                ipc=self.ipc,
                track=self.track_commands,
                acquisition=self.subtitle_acquisition,
                set_annotation_mode=self.annotation_controller.set_mode,
                draw_subtitle=self.subtitle_presentation.draw,
                seek_cue=self.subtitle_navigation.seek,
                sentence_lines=self.preview_commands.sentence_lines,
                translation=self.translation_controller,
                translation_inputs=self.translation_observation.current,
                notifications=self.notifications,
            ),
        )
        return StatelessCommandGraph(
            stateless_features(
                hover,
                mine,
                panel,
                ProfileCommandEndpoint(self.profile_session.profile),
                session,
                subtitle,
                interaction,
            ),
            STATELESS_COMMANDS,
        )

    def _assemble_subtitle_acquisition(
        self,
        submitter: JobSubmitter | None,
        stop: threading.Event,
    ) -> SubtitleAcquisitionController:
        return SubtitleAcquisitionController(
            ipc=self.ipc,
            stop=stop,
            get=self.playback_observation.query,
            notifications=self.notifications,
            track_ports=lambda: self.track_commands.ports(),
            submitter=submitter,
        )

    # --- mining -------------------------------------------------------------------------------
    def _assemble_mining_controller(
        self,
        identity: MiningIdentity,
        anki: Anki | None,
        config: MineConfig | None,
        settings: MiningOptions,
        profile: ProfileController,
        stop: threading.Event,
    ) -> MiningController:
        projection = MiningProjection(
            toast=self.toast,
            preview=self.preview_controller,
            preview_ports=lambda: self.preview_commands.ports(),
            card_source=lambda: self.preview_commands.card_source(),
            preview_enabled=lambda: self.mining_controller.show_preview,
            tooltip=self.tooltip_controller,
            tooltip_apply=self.tooltip_controller.apply_context,
            mined_here=self.sidebar_controller.mark_active_mined,
            record_mined=self.history.record_mined,
        )
        encounter = MiningEncounterSource(
            ipc=self.ipc,
            cue=self.subtitle_presentation.cue,
            tooltip=self.tooltip_controller,
            profile=profile,
            playback=self.playback_observation,
            max_bulk=settings.max_bulk,
        )
        return MiningController.for_session(
            identity,
            anki,
            config,
            MiningSessionAssembly(
                ipc=self.ipc,
                capability_submit=self._capability_submit,
                timers=self.lifecycle_timers,
                stopped=stop.is_set,
                settings=settings,
                encounter=encounter.capture,
                apply=projection.build,
            ),
        )

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
    def current_media_path(self) -> Path | None:
        """mpv's current file as an absolute path (``path`` is verbatim what was loaded, so resolve a
        relative one against ``working-directory``). None when nothing is loaded. Used by the reactive
        re-slot and the eof advance to key the #100 sibling resolver off the real filesystem path."""
        raw = self.playback_observation.value("path")
        if not raw:
            return None
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            wd = self.playback_observation.value("working-directory")
            if wd:
                p = Path(str(wd)) / p
        return p

    def install_reslot_hook(self, hook: Callable[[Path], None], *, initial: Path) -> None:
        """Follow mpv's ``file-loaded`` from now on (#100): ``hook`` re-slots the overlay onto whatever
        file mpv loads next — a native autoload/playlist advance, our own eof loadfile, or a manual
        next/prev. Seed ``initial`` (already set up by ``run_impl``) so its own file-loaded is skipped."""
        self.reslot_hook = hook
        self._slotted_path = self.current_media_path() or Path(str(initial)).expanduser()

    def _on_file_loaded(self) -> None:
        """A new file finished loading — re-slot the overlay onto it (once per distinct file). Skips the
        already-slotted file so the initial load and a redundant file-loaded don't reset stats/subs."""
        self._ass_full_probe_dirty = True
        if self.reslot_hook is None:
            return
        p = self.current_media_path()
        if p is None or p == self._slotted_path:
            return
        self._cue.replace_source(p, reason="file-loaded")
        self._slotted_path = p
        self.reslot_hook(p)

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
            self._on_file_loaded()
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
            self._on_file_loaded()
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
