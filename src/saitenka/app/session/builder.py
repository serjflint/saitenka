"""Private, one-shot construction of the owner-thread session graph."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import threading

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.config import MiningOptions, ReaderOptions
    from saitenka.app.features.analysis.analysis_controller import AnalysisCommandEndpoint
    from saitenka.app.features.annotation.annotation_controller import CueAnnotationController
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.preview.preview_controller import PreviewController
    from saitenka.app.features.sidebar.sidebar_controller import SidebarController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Coloring
    from saitenka.app.session.assembly import SessionAssembly
    from saitenka.app.session.interaction_adapter import InteractionCommandCoordinator
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.app.subtitle_render import NullRenderer, SubtitleRenderer
    from saitenka.app.toast_controller import ToastController
    from saitenka.mpvio.osd import Overlay
    from saitenka.runtime.jobs import JobSubmitter
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore

import saitenka.app.features.sidebar.sidebar as sidebar_module
import saitenka.app.session.resources as session_resources
from saitenka.app import (
    episode_reslot,
    logsetup,
    session_stats,
    subtitle_modes,
)
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
from saitenka.app.features.tooltip import tooltip_controller as tooltip_module
from saitenka.app.features.tooltip.hover_adapter import (
    HoverCommandCoordinator,
    HoverCommandPorts,
)
from saitenka.app.features.tooltip.tooltip_controller import (
    TooltipNavigationView,
    TooltipSessionActions,
    TooltipSessionContext,
    TooltipSessionView,
)
from saitenka.app.features.translation import TranslationController, TranslationObservation
from saitenka.app.interaction import mouse_capture
from saitenka.app.media import (
    tts_available,
)
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.runtime import CueCommandState
from saitenka.app.session import sidebar_coordination, surfaces
from saitenka.app.session.adapter import SessionCommandCoordinator, SessionCommandPorts
from saitenka.app.session.command_runtime import CommandRuntime, CommandRuntimePorts
from saitenka.app.session.cue_coordinator import CueCoordinator, CueOwners
from saitenka.app.session.graph import SessionGraph
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
    CUE_RETIRE_RESOURCE,
    PLAYBACK_DELTAS_PERFORMER,
    STATELESS_COMMANDS,
    SUBTITLE_REPLAY_PARTICIPANT,
    stateless_features,
)
from saitenka.app.session.stateless import StatelessCommandGraph
from saitenka.app.session.support import (
    PickerCommandEndpoint,
    SessionDiagnostics,
    SessionPresentation,
    SessionRecurrence,
)
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
    playback,
)
from saitenka.runtime.connection import ConnectionStore
from saitenka.runtime.hover import HoverDelays
from saitenka.runtime.presentation_slice import TranslationStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka_subtitles import GeometryBackend

    from saitenka.mpvio.ipc import MpvIPC

log = logging.getLogger(__name__)
console_log = logsetup.user_facing_logger()

_UNBOUND = object()


def _adopt_selected_subtitle(
    presentation: SubtitlePresentation,
    ports: Callable[[], subtitle_modes.TrackPorts],
    sid: object,
) -> None:
    """Point geometry at whatever track is primary now.

    The reset is unconditional; `on_primary_changed` re-indexes only a track it does not already
    know. mpv echoes `sid` after the selecting call has rebuilt, so a selection the session made
    itself lands in that gap and keeps its cues with no geometry source at all.
    """
    native = presentation.native
    if native is None:
        presentation.pipeline.invalidate()
        subtitle_modes.on_primary_changed(ports(), sid)
        return
    native.set_source(None, live=True)
    subtitle_modes.on_primary_changed(ports(), sid)
    if native.source_path is None and sid is not None:
        ports().rebuild_index()


class _Required[T]:
    """One construction-cycle endpoint with a named failure before binding."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._value: T | object = _UNBOUND

    def bind(self, value: T) -> T:
        if self._value is not _UNBOUND:
            raise RuntimeError(f"session endpoint already bound: {self._name}")
        self._value = value
        return value

    def get(self) -> T:
        if self._value is _UNBOUND:
            raise RuntimeError(f"session endpoint is not bound: {self._name}")
        return cast("T", self._value)


@dataclass(frozen=True, slots=True)
class _StatelessOwners:
    ipc: MpvIPC
    profile: ProfileSession
    tooltip: tooltip_module.TooltipController
    subtitles: SubtitlePresentation
    notifications: ToastController
    mining: MiningController
    playback: PlaybackObservationController
    tracks: SubtitleTrackStore
    translation_observation: TranslationObservation
    history: HistoryOwner
    analysis: AnalysisCommandEndpoint
    surfaces: LifecycleSurfaces
    sidebar: SidebarController
    picker: PickerController
    preview: PreviewCommandEndpoint
    picker_commands: PickerCommandEndpoint
    overlay: Overlay
    translation: TranslationController
    track_commands: SubtitleTrackCoordinator
    annotation: CueAnnotationController
    acquisition: SubtitleAcquisitionController
    navigation: SubtitleNavigationCoordinator


@dataclass(frozen=True, slots=True)
class _MiningOwners:
    ipc: MpvIPC
    notifications: ToastController
    preview: PreviewController
    preview_endpoint: Callable[[], PreviewCommandEndpoint]
    tooltip: tooltip_module.TooltipController
    sidebar: SidebarController
    history: HistoryOwner
    cue: CueRenderStore
    playback: PlaybackObservationController
    capability_submit: JobSubmitter | None
    timers: LifecycleTimers


def _discard(_value: object) -> None:
    pass


def _register_runtime_resources(
    ipc: MpvIPC,
    registrations: list[tuple[str, object]],
) -> None:
    names = [name for name, _resource in registrations]
    if len(names) != len(set(names)):
        raise ValueError("runtime resource names must be unique")
    for name, resource in registrations:
        if not ipc.register_session_resource(name, resource):
            raise RuntimeError(f"session runtime rejected resource: {name}")


def build_session_graph(  # noqa: PLR0913 -- resolved graph conversion is completed below
    ipc: MpvIPC,
    assembly: SessionAssembly,
    options: ReaderOptions,
    *,
    scorer: Coloring | None = None,
    anki=None,
    mine_cfg=None,
    dict_set=None,
    renderer: SubtitleRenderer | NullRenderer | None = None,
    geometry_backend: GeometryBackend | None = None,
    profile: Profile | None = None,
    tts_ok: bool | None = None,
    tooltip_runtime_jobs: Callable[
        [tooltip_module.TooltipRuntimeJobs],
        tooltip_module.TooltipRuntimeJobs,
    ]
    | None = None,
) -> SessionGraph:
    """Build and validate the complete session graph before publishing it."""
    o = options
    registrations: list[tuple[str, object]] = []
    connection = ConnectionStore(ipc)
    # Supplied by composition (`create_session_controller`), never probed off `ipc`: which egress the
    # overlay uses is a wiring decision, not something to infer from a collaborator's methods.
    overlay = assembly.overlay
    lifecycle_surfaces = assembly.surfaces
    screen = assembly.screen
    help_controller = assembly.help
    analysis_controller = assembly.analysis
    annotation_controller = assembly.annotation
    picker_controller = assembly.picker
    sidebar_controller = assembly.sidebar
    preview_controller = assembly.preview
    tooltip_preparation = assembly.tooltip_preparation
    interaction_surfaces = assembly.interaction_surfaces
    lifecycle_timers = assembly.timers
    notifications = assembly.notifications
    stop = assembly.stop
    cue_ref = _Required[CueCoordinator]("cue coordinator")
    presentation_ref = _Required[SessionPresentation]("session presentation")
    profile_integration_ref = _Required[ProfileIntegration]("profile integration")
    playback_projection_ref = _Required[PlaybackProjection]("playback projection")
    stateless_ref = _Required[StatelessCommandGraph]("stateless command graph")
    preview_ref = _Required[PreviewCommandEndpoint]("preview command endpoint")
    translation_ref = _Required[TranslationController]("translation controller")
    navigation_ref = _Required[SubtitleNavigationCoordinator]("subtitle navigation")

    def notify(text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        notifications.show(text, kind, seconds)

    def clear_subtitle_interaction() -> None:
        tooltip_controller.teardown()
        tooltip_controller.retire_selection()

    subtitle_presentation = SubtitlePresentation(
        ipc,
        settings=o.subtitle_geometry,
        visual=SubtitleVisualSettings.from_options(o.tooltip),
        renderer=renderer,
        backend=geometry_backend,
        ports=SubtitlePresentationPorts(
            target=lambda pipeline, geometry: cue_ref.get().build_target(pipeline, geometry),
            geometry=lambda: cue_ref.get().geometry_observation(),
            clear_interaction=clear_subtitle_interaction,
            redraw_cue=lambda: cue_ref.get().set_subtitle(playback_ref.get().cue.text),
            tokenize_lookahead=annotation_controller.captured_lookahead(
                lambda: cue_ref.get().annotation_inputs()
            ),
            notify=lambda text, kind: notify(text, kind, 6.0),
        ),
    )
    # Progressive startup: deps loaded on a background thread, injected on the main thread by the
    # poll loop (see load_deps_async / _apply_deps). Until then, subs render plain + a spinner shows.
    initial_profile_name = profile.name if profile is not None else "default"
    mining_identity = MiningIdentity(initial_profile_name, 0)
    # Interactive sessions publish this optional subprocess probe later; deterministic
    # demo/screenshot assembly supplies it synchronously through SessionServices.
    capability_submit = configure_runtime_jobs(ipc)
    tts_capability = (
        None
        if tts_ok is not None
        else CapabilityProbe(
            tts_available,
            name="tts",
            ttl=3_600.0,
            retry=60.0,
            submit=capability_submit,
        )
    )
    tooltip_visual = tooltip_module.TooltipVisualSettings.from_options(o.tooltip)
    history = assembly.history
    log.info(
        "layout backend: %s (requested %r)",
        tooltip_visual.backend_name,
        o.tooltip.layout_engine,
    )
    tooltip_controller = tooltip_module.TooltipController(
        ipc,
        tooltip_preparation,
        screen,
        lifecycle_timers,
        assembly.keys,
        help_controller,
        config=tooltip_module.TooltipControllerConfig(
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
    subtitle_tracks = assembly.subtitle_tracks
    profile_controller = ProfileController(
        profile,
        dict_set,
        ProfileInvalidation(
            invalidate_tokenizer=lambda: profile_integration_ref.get().invalidate_tokenizer(),
            invalidate_dictionary=lambda: profile_integration_ref.get().invalidate_dictionary(),
            reset_episode_warm=lambda: profile_integration_ref.get().reset_episode_warm(),
        ),
        ProfileSubtitles(
            current_subtitle_slang=lambda: subtitle_tracks.current.slang,
            has_subtitle_track=lambda slang: profile_integration_ref.get().has_subtitle_track(
                slang
            ),
            select_subtitle_track=lambda slang: profile_integration_ref.get().select_subtitle_track(
                slang
            ),
            retokenize_current_cue=lambda: profile_integration_ref.get().retokenize_current_cue(),
        ),
        ProfileAftermath(
            warm_episode=lambda: profile_integration_ref.get().warm_episode(),
            notify=notify,
        ),
    )

    def apply_playback_delta(delta: playback.PlaybackDelta) -> None:
        playback_projection_ref.get().apply(delta)

    playback_ref = _Required[PlaybackObservationController]("playback observation")
    playback_observation = playback_ref.bind(
        PlaybackObservationController(
            ipc,
            apply_playback_delta,
            PlaybackStartup(
                reconcile_cue=lambda text: navigation_ref.get().reconcile(text),
                refresh_render_space=lambda: presentation_ref.get().refresh_osd(),
                observe_authored_subtitle=lambda reply: (
                    subtitle_presentation.native.observe_ass_full_reply(reply)
                    if subtitle_presentation.native is not None
                    else None
                ),
                probe_display_sources=lambda reason, osd: (
                    presentation_ref.get().probe_display_sources(reason, osd)
                ),
            ),
        )
    )
    presentation = presentation_ref.bind(
        SessionPresentation(
            playback=playback_ref.get,
            screen=screen,
            subtitles=subtitle_presentation,
            tooltip=tooltip_controller,
            help_controller=help_controller,
            analysis=analysis_controller,
            sidebar=sidebar_controller,
            surfaces=lifecycle_surfaces,
            timers=lifecycle_timers,
            overlay=overlay,
        )
    )
    authored_subtitle_probe = AuthoredSubtitleProbe(
        ipc,
        playback_observation,
        subtitle_presentation,
    )
    mining_controller = _assemble_mining_controller(
        mining_identity,
        anki,
        mine_cfg,
        assembly.mining,
        profile_controller,
        stop,
        _MiningOwners(
            ipc,
            notifications,
            preview_controller,
            preview_ref.get,
            tooltip_controller,
            sidebar_controller,
            history,
            subtitle_presentation.cue,
            playback_observation,
            capability_submit,
            lifecycle_timers,
        ),
    )
    preview_commands = preview_ref.bind(
        PreviewCommandEndpoint(
            preview=preview_controller,
            help=help_controller,
            tip_keys_bound=lambda: tooltip_controller.keybindings_bound,
            mining=mining_controller,
            surfaces=lifecycle_surfaces,
            screen=screen,
            ipc=ipc,
            keys=assembly.keys,
            tip_scale_override=tooltip_controller.visual.scale_override,
            tip_max_frac=tooltip_controller.visual.base_height_fraction,
            play_audio=mining_controller.play_audio,
            cue=subtitle_presentation.cue,
            playback=playback_observation,
            toast=notify,
        )
    )
    navigation = NavigationStore()
    profile_session = ProfileSession(
        ProfileSessionAssembly(
            profile_controller,
            mining_controller,
            lifecycle_timers,
            lifecycle_surfaces,
            ProfileDependencyPorts(
                enable_async_annotation=lambda: (
                    profile_integration_ref.get().enable_async_annotation()
                ),
                dependencies_changed=lambda: profile_integration_ref.get().dependencies_changed(),
                start_prefetch=tooltip_controller.start_prefetch,
                warm_episode=lambda: profile_integration_ref.get().warm_episode(),
            ),
            lambda: tooltip_preparation.worker_count,
            lambda mode, workers: console_log.info(
                "runtime: %s · %d prefetch worker(s)", mode, workers
            ),
        ),
        identity=mining_identity,
        scorer=scorer,
    )
    analysis_observation = AnalysisObservation(
        subtitle_tracks,
        navigation,
        profile_session,
    )
    analysis_commands = analysis_controller.endpoint(analysis_observation.current)
    registrations.append(
        (
            SUBTITLE_REPLAY_PARTICIPANT,
            session_resources.Starting(
                lambda: subtitle_presentation.pipeline.connection_replaced(
                    subtitle_presentation.target()
                )
            ),
        )
    )
    surface_router = surfaces.build_surface_router(
        help_controller,
        picker_controller,
        sidebar_controller,
        preview_controller,
        tooltip_controller,
    )
    mouse = mouse_capture.MouseCapture(
        ipc,
        lifecycle_timers,
        surface_router.wants_mouse_capture,
    )
    translation_observation = TranslationObservation(
        overlay,
        tooltip_controller,
        playback_observation,
        screen,
    )

    def install_cue(
        text: str,
        *,
        revise_session_cue: bool = False,
        provisional_navigation: bool = False,
    ) -> None:
        cue_ref.get().set_subtitle(
            text,
            revise_session_cue=revise_session_cue,
            provisional_navigation=provisional_navigation,
        )

    track_commands = SubtitleTrackCoordinator(
        ipc=ipc,
        tracks=subtitle_tracks,
        navigation=navigation,
        playback=playback_observation,
        property_value=playback_observation.query,
        notifications=notifications,
        invalidate=analysis_commands.invalidate,
        translation_visible=lambda: translation_ref.get().active(translation_observation.current()),
        rebuild_index=lambda: cue_ref.get().rebuild_sub_index(),
        install_cue=install_cue,
    )
    translation_store = TranslationStore(ipc)
    translation_controller = translation_ref.bind(
        TranslationController(
            translation_store,
            lifecycle_surfaces,
            track_commands,
            auto_reveal=o.translation.auto_translate,
        )
    )
    sidebar_controller.bind_view(
        SidebarViewOwners(
            tracks=track_commands,
            playback=playback_observation,
            screen=screen,
            surfaces=lifecycle_surfaces,
            history=history,
            mining=mining_controller,
            profile=profile_session,
            analysis=analysis_controller,
            timers=lifecycle_timers,
        )
    )
    subtitle_navigation = navigation_ref.bind(
        SubtitleNavigationCoordinator(
            ipc=ipc,
            navigation=track_commands.navigation,
            geometry=lambda: subtitle_presentation.native,
            get=playback_observation.query,
            cue_text=lambda: playback_observation.cue.text,
            cue_retired=lambda: annotation_controller.view.retired,
            draw_cue=install_cue,
            replace_source=lambda path=None, *, reason: cue_ref.get().replace_source(
                path, reason=reason
            ),
            invalidate=analysis_commands.invalidate,
            warm_tokens=lambda: profile_integration_ref.get().warm_episode(),
            index_changed=sidebar_controller.index_changed,
            cue_revision=lambda: cue_ref.get().revision,
            invalidate_pipeline=subtitle_presentation.pipeline.invalidate,
        )
    )
    profile_integration = profile_integration_ref.bind(
        ProfileIntegration(
            ipc=ipc,
            profile=profile_session.profile,
            annotation=annotation_controller,
            analysis=analysis_commands,
            preparation=tooltip_preparation,
            tooltip=tooltip_controller,
            presentation=subtitle_presentation,
            tracks=subtitle_tracks,
            navigation=track_commands.navigation,
            cue_text=lambda: playback_observation.cue.text,
            annotation_inputs=lambda: cue_ref.get().annotation_inputs(),
            apply_annotation=lambda transition: cue_ref.get().apply_annotation_transition(
                transition, draw=True
            ),
            teardown_tooltip=tooltip_controller.teardown,
            retire_cue=lambda reason: cue_ref.get().retire(reason),
            configure_subtitle_mode=lambda startup, slang: cue_ref.get().configure_subtitle_mode(
                startup, slang=slang
            ),
            rebuild_index=lambda: cue_ref.get().rebuild_sub_index(),
        )
    )

    subtitle_acquisition = _assemble_subtitle_acquisition(
        ipc,
        playback_observation,
        notifications,
        track_commands.ports,
        assembly.subtitle_fetch,
        stop,
    )

    def run_stateless(command: object) -> None:
        stateless_ref.get().run(command)

    def tts_is_available() -> bool:
        return bool(tts_ok) if tts_capability is None else bool(tts_capability.value)

    def hide_tooltip() -> None:
        interaction_surfaces.remove(OverlayId.TIP)

    def observe_tooltip_session() -> TooltipSessionView:
        navigation = track_commands.navigation.current
        return TooltipSessionView(
            cue=subtitle_presentation.cue.current,
            annotation=annotation_controller.view,
            tokenizer=profile_session.profile.tokenizer,
            dictionary=profile_session.profile.dict_set,
            scorer=profile_session.scorer,
            mined=mining_controller.index_snapshot(),
            mining_target_available=mining_controller.target_available,
            subtitle_language=subtitle_tracks.current.language,
            navigation=TooltipNavigationView(navigation.sub_index, navigation.nav_idx),
            playback_cue_text=playback_observation.cue.text,
        )

    def set_annotation_hover(*, revealed: bool) -> None:
        annotation_controller.set_hover_revealed(revealed=revealed)
        subtitle_presentation.draw()

    def sync_tooltip_translation() -> None:
        translation_ref.get().sync_auto_reveal(translation_observation.current)

    tooltip_controller.bind_session_context(
        TooltipSessionContext(
            observe=observe_tooltip_session,
            read_playback=playback_observation.value,
            query_playback=playback_observation.query,
            actions=TooltipSessionActions(
                hide=hide_tooltip,
                set_annotation_hover=set_annotation_hover,
                draw_cue=subtitle_presentation.draw,
                mine_token=mining_controller.mine_token,
                preview_click=preview_commands.click,
                run_hover_command=run_stateless,
                run_mine_command=run_stateless,
                sync_translation=sync_tooltip_translation,
                record_lookup=history.record_lookup,
                toast=notify,
                tts_available=tts_is_available,
            ),
            surfaces=interaction_surfaces,
        )
    )
    screen.osd = (1280, 720)

    def settle_annotation() -> None:
        for transition in annotation_controller.settle():
            cue_ref.get().apply_annotation_transition(transition, draw=transition.publish)

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
                sidebar_controller.view(),
                build_sidebar_actions(),
                preview_commands.ports(),
                preview_commands.card_source(),
                note_id,
            ),
        )

    def download_ports() -> sub_picker.DownloadPorts:
        return sub_picker.DownloadPorts(
            notifications.show,
            subtitle_acquisition.submit,
            playback_observation.query,
            lifecycle_surfaces,
        )

    interaction = InteractionCoordinator(
        InteractionPorts(
            overlay_visible=lambda: bool(getattr(overlay, "visible", True)),
            cue_interaction_allowed=lambda: (
                cue_ref.get().command_state(retired=annotation_controller.view.retired)
                is not CueCommandState.RETIRED_AFTER_ACTIVE
            ),
            playback=playback_observation,
            router=surface_router,
            tooltip=tooltip_controller,
            sidebar=sidebar_controller,
            download=download_ports,
            sidebar_actions=build_sidebar_actions,
            hide_annotation=lambda: tooltip_controller.set_annotation_hover(revealed=False),
            settle_annotation=settle_annotation,
            sync_mouse_capture=mouse.sync,
        )
    )
    picker_commands = PickerCommandEndpoint(
        picker_controller,
        playback_observation,
        tooltip_controller,
        track_commands,
        stop,
        notifications,
    )
    stateless_commands = stateless_ref.bind(
        _assemble_stateless_commands(
            interaction.command_coordinator(),
            _StatelessOwners(
                ipc=ipc,
                profile=profile_session,
                tooltip=tooltip_controller,
                subtitles=subtitle_presentation,
                notifications=notifications,
                mining=mining_controller,
                playback=playback_observation,
                tracks=subtitle_tracks,
                translation_observation=translation_observation,
                history=history,
                analysis=analysis_commands,
                surfaces=lifecycle_surfaces,
                sidebar=sidebar_controller,
                picker=picker_controller,
                preview=preview_commands,
                picker_commands=picker_commands,
                overlay=overlay,
                translation=translation_controller,
                track_commands=track_commands,
                annotation=annotation_controller,
                acquisition=subtitle_acquisition,
                navigation=subtitle_navigation,
            ),
        )
    )
    cue_coordinator = cue_ref.bind(
        CueCoordinator(
            CueOwners(
                ipc=ipc,
                overlay=overlay,
                surfaces=lifecycle_surfaces,
                screen=screen,
                playback=playback_observation,
                presentation=subtitle_presentation,
                annotation=annotation_controller,
                tooltip=tooltip_controller,
                preview=preview_commands,
                history=history,
                tracks=subtitle_tracks,
                track_commands=track_commands,
                navigation=subtitle_navigation,
                profile=profile_session,
                analysis=analysis_commands,
                picker=picker_controller,
                acquisition=subtitle_acquisition,
                translation=translation_controller,
            )
        )
    )
    episode_watch = episode_reslot.EpisodeWatch(
        prop=playback_observation.value,
        replace_source=cue_coordinator.replace_source,
        mark_authored_probe_dirty=authored_subtitle_probe.mark_dirty,
    )

    def subtitle_selection_changed(sid: object) -> None:
        subtitle_presentation.refresh.retire()
        _adopt_selected_subtitle(subtitle_presentation, track_commands.ports, sid)

    def subtitle_timing_changed() -> None:
        if subtitle_presentation.native is not None:
            subtitle_presentation.native.record_clock_change(playback_observation.value)

    def geometry_input_changed() -> None:
        if subtitle_presentation.native is not None:
            subtitle_presentation.refresh.arm()

    def pause_changed(*, paused: bool) -> None:
        log.debug("mpv pause -> %s", paused)
        session_stats.accrue(
            history.recorder,
            paused=bool(playback_observation.value("pause")),
            language=subtitle_tracks.current.language,
        )

    def secondary_text_changed(value: object) -> None:
        inputs = translation_observation.current()
        translation_controller.secondary_text_changed(replace(inputs, secondary_text=value))

    playback_projection = playback_projection_ref.bind(
        PlaybackProjection(
            PlaybackApplication(
                retire_cue=cue_coordinator.retire,
                probe_authored_subtitle=authored_subtitle_probe.resolve,
                observe_cue=cue_coordinator.observe,
                subtitle_selection_changed=subtitle_selection_changed,
                subtitle_timing_changed=subtitle_timing_changed,
                geometry_input_changed=geometry_input_changed,
                render_space_changed=presentation.redraw_after_resize,
                end_of_file_changed=episode_watch.advance_if_reached,
                pause_changed=pause_changed,
                secondary_text_changed=secondary_text_changed,
                pointer_moved=interaction.update_hover,
            )
        )
    )

    registrations.append(
        (
            PLAYBACK_DELTAS_PERFORMER,
            session_resources.Performing(playback_projection.apply_effect),
        )
    )
    registrations.append(
        (
            CUE_RETIRE_RESOURCE,
            session_resources.Retiring(lambda: cue_coordinator.retire("connection-lost")),
        )
    )

    command_runtime = CommandRuntime(
        CommandRuntimePorts(
            ipc=ipc,
            keys=assembly.keys,
            contributed_handlers=assembly.command_handlers(),
            contributed_specs=assembly.command_specs(),
            stateless=stateless_commands,
            mining=mining_controller,
            connection=connection,
            cue=cue_coordinator,
            annotation=annotation_controller,
            help=help_controller,
            mouse=mouse,
        )
    )
    recurrence = SessionRecurrence(
        tts_capability,
        mining_controller,
        lifecycle_timers,
        history,
        playback_observation,
        subtitle_tracks,
    )
    diagnostics = SessionDiagnostics(
        ipc,
        playback_observation,
        annotation_controller,
        profile_session,
        tooltip_controller,
        subtitle_presentation,
    )
    lifecycle = compose_session_lifecycle(
        SessionLifecycleOwners(
            ipc,
            tts_capability,
            mining_controller,
            tooltip_controller,
            tooltip_preparation,
            annotation_controller,
            analysis_controller,
            mouse,
            subtitle_presentation.refresh,
            subtitle_presentation,
            history,
            lifecycle_timers,
            lifecycle_surfaces,
            overlay,
        ),
        SessionLifecycleActs(
            render_space=presentation.refresh_osd,
            start_observing=playback_observation.start_session,
            install_input=command_runtime.install_input,
            arm_capabilities=recurrence.arm_capabilities,
            announce_profile=profile_session.announce_if_ready,
            start_prefetch=tooltip_controller.start_prefetch,
            finish_mask_atlas=tooltip_preparation.finish_mask_atlas,
            history_path=lambda: playback_observation.value("path"),
            arm_history=recurrence.arm_history,
            telemetry_gauges=diagnostics.gauges,
            startup_health=diagnostics.check_startup_health,
            retire_settle_window=subtitle_navigation.retire_settle,
            finish_history=lambda: history.finish(analysis_controller.result),
            report_history=history.report,
        ),
        stop=stop,
    )
    reslot_ports = episode_reslot.ReslotPorts(
        ipc=ipc,
        finish_stats=lambda: history.finish(analysis_controller.result),
        start_stats=lambda: history.start(
            path=lambda: playback_observation.value("path"),
            arm=recurrence.arm_history,
        ),
        rebind_episode=cue_coordinator.rebind_episode,
        rebuild_index=cue_coordinator.rebuild_sub_index,
        configure_mode=cue_coordinator.configure_subtitle_mode,
        configure_retry=subtitle_acquisition.configure_retry,
        configure_picker=picker_controller.configure_listing,
        fetch_japanese=subtitle_acquisition.fetch_background,
        start_prefetch=tooltip_controller.start_prefetch,
        toast=notify,
    )
    watch_ports = episode_watch.ports()
    graph = SessionGraph(
        ipc=ipc,
        connection=connection,
        overlay=overlay,
        lifecycle_surfaces=lifecycle_surfaces,
        interaction_surfaces=interaction_surfaces,
        screen=screen,
        help=help_controller,
        analysis=analysis_controller,
        annotation=annotation_controller,
        picker=picker_controller,
        sidebar=sidebar_controller,
        preview=preview_controller,
        tooltip_preparation=tooltip_preparation,
        timers=lifecycle_timers,
        notifications=notifications,
        subtitle_presentation=subtitle_presentation,
        capability_submit=capability_submit,
        tts_capability=tts_capability,
        history=history,
        tooltip=tooltip_controller,
        subtitle_tracks=subtitle_tracks,
        playback=playback_observation,
        mining=mining_controller,
        profile=profile_session,
        analysis_observation=analysis_observation,
        analysis_commands=analysis_commands,
        subtitle_acquisition=subtitle_acquisition,
        mouse=mouse,
        translation_observation=translation_observation,
        track_commands=track_commands,
        translation=translation_controller,
        subtitle_navigation=subtitle_navigation,
        profile_integration=profile_integration,
        interaction=interaction,
        stateless_commands=stateless_commands,
        cue=cue_coordinator,
        episode_watch=episode_watch,
        commands=command_runtime,
        lifecycle=lifecycle,
        presentation=presentation,
        recurrence=recurrence,
        diagnostics=diagnostics,
        picker_commands=picker_commands,
        preview_commands=preview_commands,
        reslot=reslot_ports,
        watch=watch_ports,
    )
    _register_runtime_resources(ipc, registrations)
    return graph


def _assemble_stateless_commands(
    interaction: InteractionCommandCoordinator,
    owners: _StatelessOwners,
) -> StatelessCommandGraph:
    """Build the closed synchronous command graph from bounded authorities."""
    hover = HoverCommandCoordinator(
        HoverCommandPorts(
            profile=owners.profile.profile,
            tooltip=owners.tooltip,
            cue=owners.subtitles.cue,
            copy_token=owners.tooltip.copy_token,
            open_kanji=owners.tooltip.open_kanji,
            resume_playback=owners.tooltip.resume_after_hover_pause,
            notifications=owners.notifications,
        )
    )
    mine = MineCommandCoordinator(
        MineCommandPorts(
            mining=owners.mining,
            bookmark=BookmarkCommandEndpoint(
                playback=owners.playback,
                cue=owners.subtitles.cue,
                tracks=owners.tracks,
                tooltip=owners.tooltip,
                store=owners.history.ensure_backlog,
                property_value=owners.playback.query,
                number_property=owners.playback.number,
                sequence_property=owners.playback.sequence,
                secondary_text=owners.translation_observation.secondary_text,
                notifications=owners.notifications,
                record_capture=owners.history.record_capture,
            ),
            notifications=owners.notifications,
        )
    )
    panel = PanelCommandCoordinator(
        PanelCommandPorts(
            analysis=owners.analysis,
            surfaces=owners.surfaces,
            sidebar=owners.sidebar,
            picker=owners.picker,
            preview=owners.preview,
            retire_hover=owners.tooltip.retire_hover,
            show_sidebar=owners.sidebar.show,
            hide_sidebar=owners.sidebar.hide,
            open_picker=owners.picker_commands.run,
        )
    )

    def report_overlay_visibility(*, visible: bool) -> None:
        state = "shown" if visible else "hidden"
        send_correlated(
            owners.ipc,
            "overlay-visibility",
            "show-text",
            f"Saitenka {state}",
            2000,
        )

    session = SessionCommandCoordinator(
        SessionCommandPorts(
            overlay=owners.overlay,
            surfaces=owners.surfaces,
            subtitle_pipeline=owners.subtitles.pipeline,
            tooltip=owners.tooltip,
            translation=owners.translation,
            translation_inputs=owners.translation_observation.current,
            toggle_renderer=owners.subtitles.toggle_renderer,
            report_overlay_visibility=report_overlay_visibility,
            teardown_tip=owners.tooltip.teardown,
            subtitle_target=owners.subtitles.target,
        )
    )
    subtitle = SubtitleCommandCoordinator(
        SubtitleCommandRead(
            ipc=owners.ipc,
            navigation=owners.track_commands.navigation,
            playback=owners.playback,
            tracks=owners.tracks,
            cue=owners.subtitles.cue,
            annotation=owners.annotation,
            observed_property=owners.playback.value,
            property_value=owners.playback.query,
            text_property=owners.playback.text,
        ),
        SubtitleCommandApply(
            ipc=owners.ipc,
            track=owners.track_commands,
            acquisition=owners.acquisition,
            set_annotation_mode=owners.annotation.set_mode,
            draw_subtitle=owners.subtitles.draw,
            seek_cue=owners.navigation.seek,
            sentence_lines=owners.preview.sentence_lines,
            translation=owners.translation,
            translation_inputs=owners.translation_observation.current,
            notifications=owners.notifications,
        ),
    )
    return StatelessCommandGraph(
        stateless_features(
            hover,
            mine,
            panel,
            ProfileCommandEndpoint(owners.profile.profile),
            session,
            subtitle,
            interaction,
        ),
        STATELESS_COMMANDS,
    )


def _assemble_subtitle_acquisition(
    ipc: MpvIPC,
    playback: PlaybackObservationController,
    notifications: ToastController,
    track_ports: Callable[[], subtitle_modes.TrackPorts],
    submitter: JobSubmitter | None,
    stop: threading.Event,
) -> SubtitleAcquisitionController:
    return SubtitleAcquisitionController(
        ipc=ipc,
        stop=stop,
        get=playback.query,
        notifications=notifications,
        track_ports=track_ports,
        submitter=submitter,
    )


# --- mining -------------------------------------------------------------------------------
def _assemble_mining_controller(
    identity: MiningIdentity,
    anki: Anki | None,
    config: MineConfig | None,
    settings: MiningOptions,
    profile: ProfileController,
    stop: threading.Event,
    owners: _MiningOwners,
) -> MiningController:
    projection = MiningProjection(
        notifications=owners.notifications,
        preview=owners.preview,
        preview_endpoint=owners.preview_endpoint,
        tooltip=owners.tooltip,
        tooltip_apply=owners.tooltip.apply_context,
        mined_here=owners.sidebar.mark_active_mined,
        record_mined=owners.history.record_mined,
    )
    encounter = MiningEncounterSource(
        ipc=owners.ipc,
        cue=owners.cue,
        tooltip=owners.tooltip,
        profile=profile,
        playback=owners.playback,
        max_bulk=settings.max_bulk,
    )
    return MiningController.for_session(
        identity,
        anki,
        config,
        MiningSessionAssembly(
            ipc=owners.ipc,
            capability_submit=owners.capability_submit,
            timers=owners.timers,
            stopped=stop.is_set,
            settings=settings,
            encounter=encounter.capture,
            apply=projection.build,
        ),
    )
