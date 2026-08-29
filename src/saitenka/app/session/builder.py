"""Private, one-shot construction of the owner-thread session graph."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.config import MiningOptions, ReaderOptions
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Scorer
    from saitenka.app.session.assembly import SessionAssembly
    from saitenka.app.session.interaction_adapter import InteractionCommandCoordinator
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.runtime.jobs import JobSubmitter

import saitenka.app.features.sidebar.sidebar as sidebar_module
import saitenka.app.session.resources as session_resources
import saitenka.app.session.runtime as session_runtime
from saitenka.app import (
    episode_reslot,
    logsetup,
    session_stats,
    subtitle_intents,
    subtitle_modes,
)
from saitenka.app.bindings import LEGACY_RENDERER_MSG
from saitenka.app.capabilities import CapabilityProbe, configure_runtime_jobs
from saitenka.app.features.analysis.analysis_controller import AnalysisObservation
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
from saitenka.app.features.tooltip.tooltip_controller import TooltipSessionContext
from saitenka.app.features.translation import TranslationController, TranslationObservation
from saitenka.app.interaction import mouse_capture
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
    InteractionCoordinator,
    InteractionPorts,
)
from saitenka.app.session.lifecycle import (
    SessionLifecycleActs,
    SessionLifecycleOwners,
    compose_session_lifecycle,
)
from saitenka.app.session.panel_adapter import PanelCommandCoordinator, PanelCommandPorts
from saitenka.app.session.playback_observation import (
    AuthoredSubtitleProbe,
    PlaybackApplication,
    PlaybackObservationController,
    PlaybackProjection,
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
from saitenka.runtime import (
    Owner,
    events,
    playback,
)
from saitenka.runtime.connection import ConnectionStore
from saitenka.runtime.hover import HoverDelays
from saitenka.runtime.presentation_slice import TranslationStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.subtitles import GeometryBackend

from saitenka.app.session.turn import SessionTurn

log = logging.getLogger(__name__)
console_log = logsetup.user_facing_logger()


def _discard(_value: object) -> None:
    pass


def build_session_turn(  # noqa: PLR0913 -- resolved graph conversion is completed below
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
) -> SessionTurn:
    """Install an already-resolved session assembly without interpreting compatibility inputs."""
    turn = object.__new__(SessionTurn)
    o = options
    turn._assembly = assembly
    turn.ipc = ipc
    registrations: list[tuple[str, object]] = []
    turn._interactive_ready = False
    turn._connection = ConnectionStore(ipc)
    # Supplied by composition (`create_session_controller`), never probed off `ipc`: which egress the
    # overlay uses is a wiring decision, not something to infer from a collaborator's methods.
    turn.ov = assembly.overlay
    turn.lifecycle_surfaces = assembly.surfaces
    turn.screen = assembly.screen
    turn.help_controller = assembly.help
    turn.analysis_controller = assembly.analysis
    turn.annotation_controller = assembly.annotation
    turn.picker_controller = assembly.picker
    turn.sidebar_controller = assembly.sidebar
    turn.preview_controller = assembly.preview
    turn.tooltip_preparation = assembly.tooltip_preparation
    # Hand teardown to the runtime at the point of construction, so the lifetime belongs to
    # whoever owns it rather than to a line in a teardown table far away. We keep *using* it;
    # what moves is when it closes. False means no runtime owns this session, and the close
    # table's fallback still has to run.
    # `getattr`, like the job-lane port below: a partial IPC (the benches' fake) constructs a
    # SessionController without implementing every runtime port, and construction must not demand one.

    turn.interaction_surfaces = assembly.interaction_surfaces
    turn.lifecycle_timers = assembly.timers
    turn.notifications = assembly.notifications
    stop = assembly.stop

    def clear_subtitle_interaction() -> None:
        turn.tooltip_controller.teardown()
        turn.tooltip_controller.retire_selection()

    turn.subtitle_presentation = SubtitlePresentation(
        ipc,
        settings=o.subtitle_geometry,
        visual=SubtitleVisualSettings.from_options(o.tooltip),
        renderer=renderer,
        backend=geometry_backend,
        ports=SubtitlePresentationPorts(
            target=turn._build_subtitle_target,
            geometry=turn._geometry_observation,
            clear_interaction=clear_subtitle_interaction,
            redraw_cue=lambda: turn.set_subtitle(turn.playback_observation.cue.text),
            tokenize_lookahead=turn.annotation_controller.captured_lookahead(
                turn._annotation_inputs
            ),
        ),
    )
    # Progressive startup: deps loaded on a background thread, injected on the main thread by the
    # poll loop (see load_deps_async / _apply_deps). Until then, subs render plain + a spinner shows.
    initial_profile_name = profile.name if profile is not None else "default"
    mining_identity = MiningIdentity(initial_profile_name, 0)
    # Interactive sessions publish this optional subprocess probe later; deterministic
    # demo/screenshot assembly supplies it synchronously through SessionServices.
    turn._capability_submit = configure_runtime_jobs(ipc)
    turn._tts_capability = (
        None
        if tts_ok is not None
        else CapabilityProbe(
            tts_available,
            name="tts",
            ttl=3_600.0,
            retry=60.0,
            submit=turn._capability_submit,
        )
    )
    tooltip_visual = tooltip_controller.TooltipVisualSettings.from_options(o.tooltip)
    turn.history = assembly.history
    log.info(
        "layout backend: %s (requested %r)",
        tooltip_visual.backend_name,
        o.tooltip.layout_engine,
    )
    turn.tooltip_controller = tooltip_controller.TooltipController(
        ipc,
        turn.tooltip_preparation,
        turn.screen,
        turn.lifecycle_timers,
        assembly.keys,
        turn.help_controller,
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
    turn._subtitle_tracks = assembly.subtitle_tracks
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
            current_subtitle_slang=lambda: turn._subtitle_tracks.current.slang,
            has_subtitle_track=lambda slang: profile_integration.has_subtitle_track(slang),
            select_subtitle_track=lambda slang: profile_integration.select_subtitle_track(slang),
            retokenize_current_cue=lambda: profile_integration.retokenize_current_cue(),
        ),
        ProfileAftermath(
            warm_episode=lambda: profile_integration.warm_episode(),
            notify=lambda text, kind: turn.toast(text, kind),
        ),
    )
    turn._mouse_in = False  # cursor over the video window — an engagement signal
    turn._scrolled_this_tick = False  # a wheel/tip-scroll ran this poll tick — for render-span
    # attribution (did hover-driven scan/nested-popup work land in the same tick as a scroll?)
    playback_projection: PlaybackProjection | None = None

    def apply_playback_delta(delta: playback.PlaybackDelta) -> None:
        if playback_projection is None:
            raise RuntimeError("playback projection is not bound")
        playback_projection.apply(delta)

    turn.playback_observation = PlaybackObservationController(
        turn.ipc,
        apply_playback_delta,
        PlaybackStartup(
            reconcile_cue=lambda text: turn.subtitle_navigation.reconcile(text),
            refresh_render_space=turn.refresh_osd,
            observe_authored_subtitle=lambda reply: (
                turn.subtitle_presentation.native.observe_ass_full_reply(reply)
                if turn.subtitle_presentation.native is not None
                else None
            ),
            probe_display_sources=turn._probe_display_sources,
        ),
    )
    authored_subtitle_probe = AuthoredSubtitleProbe(
        turn.ipc,
        turn.playback_observation,
        turn.subtitle_presentation,
    )
    turn.mining_controller = _assemble_mining_controller(
        turn,
        mining_identity,
        anki,
        mine_cfg,
        assembly.mining,
        profile_controller,
        stop,
    )
    navigation = NavigationStore()
    turn.profile_session = ProfileSession(
        ProfileSessionAssembly(
            profile_controller,
            turn.mining_controller,
            turn.lifecycle_timers,
            turn.lifecycle_surfaces,
            ProfileDependencyPorts(
                enable_async_annotation=lambda: profile_integration.enable_async_annotation(),
                dependencies_changed=lambda: profile_integration.dependencies_changed(),
                start_prefetch=turn.tooltip_controller.start_prefetch,
                warm_episode=lambda: profile_integration.warm_episode(),
            ),
            lambda: turn.tooltip_preparation.worker_count,
            lambda mode, workers: console_log.info(
                "runtime: %s · %d prefetch worker(s)", mode, workers
            ),
        ),
        identity=mining_identity,
        scorer=scorer,
    )
    turn.analysis_observation = AnalysisObservation(
        turn._subtitle_tracks,
        navigation,
        turn.profile_session,
    )
    turn.analysis_commands = turn.analysis_controller.endpoint(turn.analysis_observation.current)
    # The subtitle raster, retired at `RENDERING`. `native_geometry` is installed after this
    # point, so every one of these resolves it when it closes rather than now.
    # The two connection acts. Registered here with the rest and late-bound for the same
    # reason: both read collaborators this constructor has not finished building.
    registrations.append(
        (
            SUBTITLE_REPLAY_PARTICIPANT,
            # Late-bound like every other registered step: an early-bound method also freezes the
            # seam a test replaces, and these two are reached only through the effect.
            session_resources.Starting(lambda: turn._on_ipc_reconnect()),
        )
    )
    # `Owner.SUBTITLE`'s slice: which mpv track plays which role. Session-lived like the
    # playback one, and episode-safe because a re-slot always runs `configure_subtitle_mode`,
    # whose event resets the whole state.
    turn.subtitle_acquisition = _assemble_subtitle_acquisition(
        turn,
        assembly.subtitle_fetch,
        stop,
    )
    surface_router = surfaces.build_surface_router(
        turn.help_controller,
        turn.picker_controller,
        turn.sidebar_controller,
        turn.preview_controller,
        turn.tooltip_controller,
    )
    turn._mouse = mouse_capture.MouseCapture(
        ipc,
        turn.lifecycle_timers,
        surface_router.wants_mouse_capture,
    )
    turn.translation_observation = TranslationObservation(
        turn.ov,
        turn.tooltip_controller,
        turn.playback_observation,
        turn.screen,
    )
    translation_controller: TranslationController
    turn.track_commands = SubtitleTrackCoordinator(
        ipc=turn.ipc,
        tracks=turn._subtitle_tracks,
        navigation=navigation,
        playback=turn.playback_observation,
        property_value=turn.playback_observation.query,
        notifications=turn.notifications,
        invalidate=turn.analysis_commands.invalidate,
        translation_visible=lambda: translation_controller.active(
            turn.translation_observation.current()
        ),
        rebuild_index=turn.rebuild_sub_index,
        install_cue=turn.set_subtitle,
    )
    translation_store = TranslationStore(turn.ipc)
    translation_controller = TranslationController(
        translation_store,
        turn.lifecycle_surfaces,
        turn.track_commands,
        auto_reveal=o.translation.auto_translate,
    )
    turn.translation_controller = translation_controller
    turn.sidebar_controller.bind_view(
        SidebarViewOwners(
            tracks=turn.track_commands,
            playback=turn.playback_observation,
            screen=turn.screen,
            surfaces=turn.lifecycle_surfaces,
            history=turn.history,
            mining=turn.mining_controller,
            profile=turn.profile_session,
            analysis=turn.analysis_controller,
            timers=turn.lifecycle_timers,
        )
    )
    turn.subtitle_navigation = SubtitleNavigationCoordinator(
        ipc=turn.ipc,
        navigation=turn.track_commands.navigation,
        geometry=lambda: turn.subtitle_presentation.native,
        get=turn.playback_observation.query,
        cue_text=lambda: turn.playback_observation.cue.text,
        cue_retired=lambda: turn.annotation_controller.view.retired,
        draw_cue=turn.set_subtitle,
        replace_source=lambda path=None, *, reason: turn._cue.replace_source(path, reason=reason),
        invalidate=turn.analysis_commands.invalidate,
        warm_tokens=lambda: profile_integration.warm_episode(),
        index_changed=turn.sidebar_controller.index_changed,
        cue_revision=lambda: turn.cue_revision,
        invalidate_pipeline=turn.subtitle_presentation.pipeline.invalidate,
    )
    profile_integration = ProfileIntegration(
        ipc=turn.ipc,
        profile=turn.profile_session.profile,
        annotation=turn.annotation_controller,
        analysis=turn.analysis_commands,
        preparation=turn.tooltip_preparation,
        tooltip=turn.tooltip_controller,
        presentation=turn.subtitle_presentation,
        tracks=turn._subtitle_tracks,
        navigation=turn.track_commands.navigation,
        cue_text=lambda: turn.playback_observation.cue.text,
        annotation_inputs=turn._annotation_inputs,
        apply_annotation=lambda transition: turn._apply_annotation_transition(
            transition, draw=True
        ),
        teardown_tooltip=turn.tooltip_controller.teardown,
        retire_cue=lambda reason: turn._cue.retire(reason),
        configure_subtitle_mode=lambda startup, slang: turn.configure_subtitle_mode(
            startup, slang=slang
        ),
        rebuild_index=turn.rebuild_sub_index,
    )
    turn.profile_integration = profile_integration

    stateless_commands: StatelessCommandGraph

    def run_stateless(command: object) -> None:
        stateless_commands.run(command)

    tts_capability = turn._tts_capability

    def tts_is_available() -> bool:
        return bool(tts_ok) if tts_capability is None else bool(tts_capability.value)

    def hide_tooltip() -> None:
        turn.interaction_surfaces.remove(OverlayId.TIP)

    turn.tooltip_controller.bind_session_context(
        TooltipSessionContext(
            hide_tooltip=hide_tooltip,
            surfaces=turn.interaction_surfaces,
            screen=turn.screen,
            preparation=turn.tooltip_preparation,
            annotation=turn.annotation_controller,
            presentation=turn.subtitle_presentation,
            profile=turn.profile_session,
            mining=turn.mining_controller,
            playback=turn.playback_observation,
            translation=turn.translation_controller,
            translation_observation=turn.translation_observation,
            history=turn.history,
            notifications=turn.notifications,
            tracks=turn._subtitle_tracks,
            navigation=turn.track_commands.navigation,
            preview_click=turn.preview_commands.click,
            run_hover_command=run_stateless,
            run_mine_command=run_stateless,
            tts_available=tts_is_available,
        )
    )
    turn.screen.osd = (1280, 720)
    # Normalized source of a cue drawn PLAIN because its annotation can't complete yet (dicts
    # loading); reader_deps re-renders it annotated once deps land. None = drawn annotated.
    turn._nudge_pending = False  # a draw happened while paused → re-flush the OSD next tick (#8172)

    def settle_annotation() -> None:
        for transition in turn.annotation_controller.settle():
            turn._apply_annotation_transition(transition, draw=transition.publish)

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
                turn.sidebar_controller.view(),
                build_sidebar_actions(),
                turn.preview_commands.ports(),
                turn.preview_commands.card_source(),
                note_id,
            ),
        )

    def download_ports() -> sub_picker.DownloadPorts:
        return sub_picker.DownloadPorts(
            turn.toast,
            turn.subtitle_acquisition.submit,
            turn.playback_observation.query,
            turn.lifecycle_surfaces,
        )

    turn.interaction = InteractionCoordinator(
        InteractionPorts(
            overlay_visible=lambda: bool(getattr(turn.ov, "visible", True)),
            playback=turn.playback_observation,
            router=surface_router,
            tooltip=turn.tooltip_controller,
            sidebar=turn.sidebar_controller,
            download=download_ports,
            sidebar_actions=build_sidebar_actions,
            hide_annotation=lambda: turn.tooltip_controller.set_annotation_hover(revealed=False),
            settle_annotation=settle_annotation,
            sync_mouse_capture=turn._mouse.sync,
        )
    )
    stateless_commands = _assemble_stateless_commands(turn, turn.interaction.command_coordinator())
    turn._stateless_commands = stateless_commands
    turn._cue = CueCoordinator(
        CueTransactions(
            settle_interaction=turn.interaction.settle,
            current_text=lambda: turn.playback_observation.cue.text,
            reconcile_text=turn.subtitle_navigation.reconcile,
            revision=lambda: turn.cue_revision,
            reduce_playback=turn.playback_observation.dispatch,
            retire_settle_window=turn.subtitle_navigation.retire_settle,
            retire_annotation_cue=turn.annotation_controller.retire_cue,
            teardown_tooltip=turn.tooltip_controller.teardown,
            retire_tooltip_selection=turn.tooltip_controller.retire_selection,
            reset_cue_render=turn.subtitle_presentation.cue.reset,
            close_picker=turn.picker_controller.close,
            retire_acquisition_episode=turn.subtitle_acquisition.retire_episode,
            retire_annotation_warm=turn.annotation_controller.retire_episode_warm,
            retire_translation_episode=turn.translation_controller.retire_episode,
            playback_routed=lambda: turn.playback_observation.routed,
            retire_playback_episode=turn.playback_observation.retire_episode,
            retire_subtitle_episode=lambda: _discard(
                turn._subtitle_tracks.dispatch(events.EpisodeRetired())
            ),
            retire_tooltip_episode=turn.tooltip_controller.retire_episode,
            replace_navigation=turn.track_commands.navigation.replace,
        )
    )
    turn.episode_watch = episode_reslot.EpisodeWatch(
        prop=turn.playback_observation.value,
        replace_source=turn._cue.replace_source,
        mark_authored_probe_dirty=authored_subtitle_probe.mark_dirty,
    )

    def subtitle_selection_changed(sid: object) -> None:
        turn.subtitle_presentation.refresh.retire()
        if turn.subtitle_presentation.native is not None:
            turn.subtitle_presentation.native.set_source(None, live=True)
        else:
            turn.subtitle_presentation.pipeline.invalidate()
        subtitle_modes.on_primary_changed(turn.track_commands.ports(), sid)

    def subtitle_timing_changed() -> None:
        if turn.subtitle_presentation.native is not None:
            turn.subtitle_presentation.native.record_clock_change(turn.playback_observation.value)

    def geometry_input_changed() -> None:
        if turn.subtitle_presentation.native is not None:
            turn.subtitle_presentation.refresh.arm()

    def pause_changed(*, paused: bool) -> None:
        log.debug("mpv pause -> %s", paused)
        session_stats.accrue(
            turn.history.recorder,
            paused=bool(turn.playback_observation.value("pause")),
            language=turn._subtitle_tracks.current.language,
        )

    def secondary_text_changed(value: object) -> None:
        inputs = turn.translation_observation.current()
        turn.translation_controller.secondary_text_changed(replace(inputs, secondary_text=value))

    playback_projection = PlaybackProjection(
        PlaybackApplication(
            retire_cue=turn._cue.retire,
            probe_authored_subtitle=authored_subtitle_probe.resolve,
            observe_cue=turn._cue.observe,
            subtitle_selection_changed=subtitle_selection_changed,
            subtitle_timing_changed=subtitle_timing_changed,
            geometry_input_changed=geometry_input_changed,
            render_space_changed=turn._redraw_after_resize,
            end_of_file_changed=turn.episode_watch.advance_if_reached,
            pause_changed=pause_changed,
            secondary_text_changed=secondary_text_changed,
            pointer_moved=turn.interaction.update_hover,
        )
    )

    def file_loaded() -> None:
        turn.episode_watch.file_loaded()

    registrations.append((RESLOT_PARTICIPANT, session_resources.Starting(file_loaded)))
    registrations.append(
        (
            PLAYBACK_DELTAS_PERFORMER,
            session_resources.Performing(playback_projection.apply_effect),
        )
    )
    registrations.append(
        (
            CUE_RETIRE_RESOURCE,
            session_resources.Retiring(lambda: turn._cue.retire("connection-lost")),
        )
    )

    turn.command_runtime = CommandRuntime(
        CommandRuntimePorts(
            ipc=turn.ipc,
            keys=turn._assembly.keys,
            contributed_handlers=turn._assembly.command_handlers(),
            contributed_specs=turn._assembly.command_specs(),
            stateless=stateless_commands,
            toggle_renderer=turn.subtitle_presentation.toggle_renderer,
            mining=turn.mining_controller,
            connection=turn._connection,
            cue=turn._cue,
            annotation=turn.annotation_controller,
            help=turn.help_controller,
            mouse=turn._mouse,
        ),
        legacy_renderer_message=LEGACY_RENDERER_MSG,
    )
    registrations.append(
        (COMMAND_PERFORMER, session_resources.Performing(turn.command_runtime.run_effect))
    )

    turn._lifecycle = compose_session_lifecycle(
        SessionLifecycleOwners(
            ipc,
            turn._tts_capability,
            turn.mining_controller,
            turn.tooltip_controller,
            turn.tooltip_preparation,
            turn.annotation_controller,
            turn.analysis_controller,
            turn._mouse,
            turn.subtitle_presentation.refresh,
            turn.subtitle_presentation,
            turn.history,
            turn.lifecycle_timers,
            turn.lifecycle_surfaces,
            turn.ov,
        ),
        SessionLifecycleActs(
            render_space=turn.refresh_osd,
            start_observing=turn.playback_observation.start_session,
            install_input=turn.command_runtime.install_input,
            arm_capabilities=turn.arm_capability_refresh,
            start_prefetch=turn.tooltip_controller.start_prefetch,
            finish_mask_atlas=turn.tooltip_preparation.finish_mask_atlas,
            history_path=lambda: turn.playback_observation.value("path"),
            arm_history=turn.arm_session_persist,
            telemetry_gauges=turn._telemetry_gauges,
            startup_health=turn._check_startup_health,
            retire_settle_window=turn.subtitle_navigation.retire_settle,
            finish_history=lambda: turn.history.finish(turn.analysis_controller.result),
            report_history=turn.history.report,
        ),
        registrations=registrations,
        stop=stop,
    )
    facts = session_runtime.SessionFacts(
        refresh_osd=turn.refresh_osd,
        prop=turn.playback_observation.value,
        get=turn.playback_observation.query,
        tokens=lambda: turn.subtitle_presentation.cue.current.tokens,
        is_content_token=lambda token: turn.profile_session.profile.tokenizer.is_content(token),
        osd_height=lambda: turn.screen.osd[1],
        painted=lambda: turn.lifecycle_surfaces.settled() and turn.interaction_surfaces.settled(),
    )
    acts = session_runtime.SessionActs(
        drive_annotation_once=turn._drive_annotation_once,
        prepare_subtitle=turn.prepare_subtitle_blocking,
        prepare_hover=turn.tooltip_controller.prepare_hover_blocking,
        mark_ready=turn._mark_interactive_ready,
        scroll_tip=turn.tooltip_controller.scroll_tip,
        toggle_translation=turn._stateless_commands.handler(
            subtitle_intents.SubtitleCommand.TOGGLE_TRANSLATION
        ),
        mine_current=turn._stateless_commands.handler(mine_intents.MineCommand.WORD),
        bulk_mine=turn._stateless_commands.handler(mine_intents.MineCommand.EPISODE),
    )
    turn.entry_runtime = SessionRuntime(facts, acts, turn.ipc)
    return turn


def _assemble_stateless_commands(
    turn: SessionTurn,
    interaction: InteractionCommandCoordinator,
) -> StatelessCommandGraph:
    """Build the closed synchronous command graph from bounded authorities."""
    hover = HoverCommandCoordinator(
        HoverCommandPorts(
            profile=turn.profile_session.profile,
            tooltip=turn.tooltip_controller,
            cue=turn.subtitle_presentation.cue,
            copy_token=turn.tooltip_controller.copy_token,
            open_kanji=turn.tooltip_controller.open_kanji,
            resume_playback=turn.tooltip_controller.resume_after_hover_pause,
            notifications=turn.notifications,
        )
    )
    mine = MineCommandCoordinator(
        MineCommandPorts(
            mining=turn.mining_controller,
            bookmark=BookmarkCommandEndpoint(
                playback=turn.playback_observation,
                cue=turn.subtitle_presentation.cue,
                tracks=turn._subtitle_tracks,
                tooltip=turn.tooltip_controller,
                store=turn.history.ensure_backlog,
                property_value=turn.playback_observation.query,
                number_property=turn.playback_observation.number,
                sequence_property=turn.playback_observation.sequence,
                secondary_text=turn.translation_observation.secondary_text,
                notifications=turn.notifications,
                record_capture=turn.history.record_capture,
            ),
            notifications=turn.notifications,
        )
    )
    panel = PanelCommandCoordinator(
        PanelCommandPorts(
            analysis=turn.analysis_commands,
            surfaces=turn.lifecycle_surfaces,
            sidebar=turn.sidebar_controller,
            picker=turn.picker_controller,
            preview=turn.preview_commands,
            retire_hover=turn.tooltip_controller.retire_hover,
            show_sidebar=turn.sidebar_controller.show,
            hide_sidebar=turn.sidebar_controller.hide,
            open_picker=turn._open_picker_command,
        )
    )
    session = SessionCommandCoordinator(
        SessionCommandPorts(
            overlay=turn.ov,
            surfaces=turn.lifecycle_surfaces,
            subtitle_pipeline=turn.subtitle_presentation.pipeline,
            tooltip=turn.tooltip_controller,
            translation=turn.translation_controller,
            translation_inputs=turn.translation_observation.current,
            teardown_tip=turn.tooltip_controller.teardown,
            subtitle_target=turn.subtitle_presentation.target,
        )
    )
    subtitle = SubtitleCommandCoordinator(
        SubtitleCommandRead(
            ipc=turn.ipc,
            navigation=turn.track_commands.navigation,
            playback=turn.playback_observation,
            tracks=turn._subtitle_tracks,
            cue=turn.subtitle_presentation.cue,
            annotation=turn.annotation_controller,
            observed_property=turn.playback_observation.value,
            property_value=turn.playback_observation.query,
            text_property=turn.playback_observation.text,
        ),
        SubtitleCommandApply(
            ipc=turn.ipc,
            track=turn.track_commands,
            acquisition=turn.subtitle_acquisition,
            set_annotation_mode=turn.annotation_controller.set_mode,
            draw_subtitle=turn.subtitle_presentation.draw,
            seek_cue=turn.subtitle_navigation.seek,
            sentence_lines=turn.preview_commands.sentence_lines,
            translation=turn.translation_controller,
            translation_inputs=turn.translation_observation.current,
            notifications=turn.notifications,
        ),
    )
    return StatelessCommandGraph(
        stateless_features(
            hover,
            mine,
            panel,
            ProfileCommandEndpoint(turn.profile_session.profile),
            session,
            subtitle,
            interaction,
        ),
        STATELESS_COMMANDS,
    )


def _assemble_subtitle_acquisition(
    turn: SessionTurn,
    submitter: JobSubmitter | None,
    stop: threading.Event,
) -> SubtitleAcquisitionController:
    return SubtitleAcquisitionController(
        ipc=turn.ipc,
        stop=stop,
        get=turn.playback_observation.query,
        notifications=turn.notifications,
        track_ports=lambda: turn.track_commands.ports(),
        submitter=submitter,
    )


# --- mining -------------------------------------------------------------------------------
def _assemble_mining_controller(
    turn: SessionTurn,
    identity: MiningIdentity,
    anki: Anki | None,
    config: MineConfig | None,
    settings: MiningOptions,
    profile: ProfileController,
    stop: threading.Event,
) -> MiningController:
    projection = MiningProjection(
        toast=turn.toast,
        preview=turn.preview_controller,
        preview_ports=lambda: turn.preview_commands.ports(),
        card_source=lambda: turn.preview_commands.card_source(),
        preview_enabled=lambda: turn.mining_controller.show_preview,
        tooltip=turn.tooltip_controller,
        tooltip_apply=turn.tooltip_controller.apply_context,
        mined_here=turn.sidebar_controller.mark_active_mined,
        record_mined=turn.history.record_mined,
    )
    encounter = MiningEncounterSource(
        ipc=turn.ipc,
        cue=turn.subtitle_presentation.cue,
        tooltip=turn.tooltip_controller,
        profile=profile,
        playback=turn.playback_observation,
        max_bulk=settings.max_bulk,
    )
    return MiningController.for_session(
        identity,
        anki,
        config,
        MiningSessionAssembly(
            ipc=turn.ipc,
            capability_submit=turn._capability_submit,
            timers=turn.lifecycle_timers,
            stopped=stop.is_set,
            settings=settings,
            encounter=encounter.capture,
            apply=projection.build,
        ),
    )
