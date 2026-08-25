"""The live study-session controller and its explicit feature composition.

``SessionController`` owns session ordering and mpv mutation. Bounded collaborators own feature state and policy;
the controller assembles their current inputs and coordinates cross-feature turns.
"""

from __future__ import annotations

import logging
import threading
import time
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from concurrent.futures import Future

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Scorer
    from saitenka.app.session_assembly import SessionAssembly
    from saitenka.runtime.card_preview import CardPreview
    from saitenka.runtime.help import HelpState
    from saitenka.runtime.picker import PickerState
    from saitenka.runtime.sidebar import SidebarState

from saitenka import otel_metrics
from saitenka.app import (
    analysis_overlay,
    backlog,
    card_preview,
    cue_annotation,
    episode_reslot,
    geometry_refresh,
    hover_intents,
    hover_snapshot,
    logsetup,
    mask_atlas_startup,
    mine_intents,
    mined_feedback,
    miner,
    miner_ui,
    mouse_capture,
    native_subtitles,
    nested_popup,
    panel_intents,
    prefetch,
    profile_intents,
    reader_deps,
    session_intents,
    session_resources,
    session_runtime,
    session_stats,
    sidebar,
    sub_picker,
    subnav,
    subnav_settle,
    subtitle_intents,
    subtitle_modes,
    subtitle_raster,
    subtitles,
    surfaces,
    telemetry,
    tooltip,
    tooltip_controller,
    tooltip_engaged,
    tooltip_panel,
    translation,
)
from saitenka.app import sidebar as sidebar_module
from saitenka.app import sub_picker as sub_picker_module
from saitenka.app.bindings import (
    ANALYSIS_MSG,
    ANNOTATION_MSG,
    BOOKMARK_MSG,
    CLICK_MSG,
    COPY_CLICK_MSG,
    COPY_LINE_MSG,
    COPY_MSG,
    GLOBAL_SECTION,
    HOVER_PAUSE_MSG,
    KANJI_MSG,
    LEGACY_RENDERER_MSG,
    MINE_ALL_MSG,
    MINE_MSG,
    MINE_VIDEO_MSG,
    OVERLAY_TOGGLE_MSG,
    PREVIEW_CLOSE_MSG,
    PREVIEW_MSG,
    PROFILE_CYCLE_MSG,
    SCROLL_DOWN_MSG,
    SCROLL_UP_MSG,
    SIDEBAR_MSG,
    SPEAK_MSG,
    SUB_ANCHOR_MSG,
    SUB_NEXT_MSG,
    SUB_PICKER_MSG,
    SUB_PREV_MSG,
    SUB_REPLAY_MSG,
    SUBTITLE_LANGUAGE_MSG,
    SUBTITLE_MARK_JP_MSG,
    SUBTITLE_RETRY_MSG,
    TIP_CLOSE_MSG,
    TIP_DOWN_MSG,
    TIP_UP_MSG,
    TRANS_MSG,
    active_bindings,
    section_contents,
)
from saitenka.app.capabilities import CapabilityProbe, configure_runtime_jobs
from saitenka.app.close_ledger import CloseLedger, CloseStep, fallback_after
from saitenka.app.config import ReaderOptions
from saitenka.app.interaction_intents import InteractionCommand
from saitenka.app.interaction_surfaces import InteractionSurfaces
from saitenka.app.languages import MAIN_LANG, SECOND_LANG
from saitenka.app.lifecycle_timers import LifecycleTimerKind, LifecycleTimers
from saitenka.app.media import (
    tts_available,
)
from saitenka.app.miner import MineCue
from saitenka.app.mining_controller import (
    ForceDuplicate,
    MiningController,
    MiningIdentity,
    MiningSessionAssembly,
)
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.paths import cache_dir
from saitenka.app.perf import gil_disabled
from saitenka.app.popups import (
    ClickPorts,
    HoverActions,
    HoverInputs,
    Panel,
    PopupView,
    ShowActions,
    TipPorts,
    TooltipState,
    WordLookup,
)
from saitenka.app.profile_controller import (
    ProfileAftermath,
    ProfileController,
    ProfileEnvironment,
    ProfileInvalidation,
    ProfileSubtitles,
)
from saitenka.app.reader_context import (
    EpisodeContext,
    InteractionContext,
    RenderCacheState,
    SessionContext,
)
from saitenka.app.runtime import (
    COMMAND_SPECS,
    CommandExecution,
    CommandExecutor,
    CommandOutcome,
    CommandPolicy,
    CueCommandState,
)
from saitenka.app.session_routes import (
    BACKLOG_RESOURCE,
    CAPABILITY_PARTICIPANTS,
    COLLABORATORS_PARTICIPANT,
    COMMAND_PERFORMER,
    CUE_RETIRE_RESOURCE,
    DIAGNOSTICS_PARTICIPANT,
    HISTORY_PARTICIPANT,
    INPUT_CAPTURE_RESOURCE,
    INPUT_PARTICIPANT,
    INTERACTION_WORK_PARTICIPANTS,
    MINED_RESOURCE,
    OBSERVERS_PARTICIPANT,
    OVERLAY_RESOURCE,
    PLAYBACK_DELTAS_PERFORMER,
    RENDER_GUARD_PARTICIPANT,
    RENDER_SPACE_PARTICIPANT,
    RESLOT_PARTICIPANT,
    SESSION_SUMMARY_RESOURCE,
    SUBTITLE_CLEAR_RESOURCE,
    SUBTITLE_CLOSE_RESOURCE,
    SUBTITLE_DEACTIVATE_RESOURCE,
    SUBTITLE_REPLAY_PARTICIPANT,
    SURFACES_RESOURCE,
    WORKER_LANE_PARTICIPANTS,
    stateless_features,
)
from saitenka.app.session_runtime import SessionEntry, SessionRuntime
from saitenka.app.stateless import StatelessRouter
from saitenka.app.subtitle_geometry_job import GEOMETRY_LANE, SubtitleGeometryWorker
from saitenka.app.subtitle_geometry_job import (
    configure_runtime_job as configure_geometry_lane,
)
from saitenka.app.subtitle_pipeline import CurrentSubtitleRenderer, SubtitleModeCoordinator
from saitenka.app.subtitle_render import (
    DrawRequest,
    NativeVisibleRenderer,
    NullRenderer,
    SubtitleRenderer,
    SubtitleTarget,
)
from saitenka.app.toast import render_toast
from saitenka.app.token_cache import TokenCache, TokenizedCue, cue_key
from saitenka.mpvio.gateway import register_observer_set
from saitenka.render.layout_backend import backend_label, resolve_backend
from saitenka.runtime import (
    ClosePhase,
    CommandHandled,
    CommandReason,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EffectFinished,
    EffectOutcome,
    Owner,
    SessionClosing,
    StartupReady,
    UserCommand,
    events,
    playback,
)
from saitenka.runtime import subtitle as subtitle_state
from saitenka.runtime.connection import ConnectionStore
from saitenka.runtime.effects import ApplyPlaybackDeltas, RunUserCommand
from saitenka.runtime.hover import HoverDelays
from saitenka.runtime.interaction_slice import (
    PickerStore,
    PreviewStore,
    SidebarStore,
)
from saitenka.runtime.playback_slice import PlaybackReducer, PlaybackSlice, PlaybackStore
from saitenka.runtime.presentation_slice import TranslationStore
from saitenka.runtime.runner import SessionRunner
from saitenka.runtime.subtitle_slice import SubtitleTrackStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.render_cache import RenderCache
    from saitenka.app.tokenize import Token
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.subtitles import CueIndex, GeometryBackend

log = logging.getLogger(__name__)
#: For the two lines the user is meant to read on the terminal (logsetup.CONSOLE_LOGGER_NAME).
console_log = logsetup.user_facing_logger()

# Local names for the shared OSD slot registry (overlay_ids.OverlayId is the single source of truth
# so extracted subsystems can't collide on slot numbers). IntEnum → drop-in int at every call site.
SUB_ID = OverlayId.SUB
TIP_ID = OverlayId.TIP
TOAST_ID = OverlayId.TOAST
NESTED_ID = OverlayId.NESTED
# The nested popup gets its own (roomier) height cap (TooltipOptions.nested_max_frac) so shrinking
# the base tooltip (tip_max_frac) doesn't cramp the deep-dive.


# Properties the poll loop consumes event-driven (observe_property) instead of issuing 3–5
# blocking get_property round-trips per 25 ms tick. One initial read seeds pre-observe state.
OBSERVED_PROPS = (
    "sub-text",
    "sub-text/ass-full",
    "mouse-pos",
    "osd-dimensions",
    "pause",
    "secondary-sub-text",
    "sid",
    "sub-start",
    "sub-end",
    "sub-delay",
    "time-pos",
    "video-out-params",
    "options/sub-ass-override",
    "options/sub-ass-scale-with-window",
    "options/sub-scale",
    "options/sub-pos",
    "options/sub-use-margins",
    "options/sub-ass-force-margins",
    "options/sub-ass-video-aspect-override",
    "options/sub-ass-use-video-data",
    "options/sub-ass-style-overrides",
    "options/sub-scale-with-window",
    "options/sub-scale-by-window",
    "options/blend-subtitles",
    "options/sub-filter-sdh",
    "options/sub-font-provider",
    "options/embeddedfonts",
    "options/sub-fonts-dir",
    "options/sub-font",
    "options/osd-fonts-dir",
    "options/osd-font-provider",
    # `converted.STYLE_OPTIONS` — the style mpv applies to a track it converted. Observed so
    # `_render_inputs` does not block on them per cue, and counted a render-space input so a
    # mid-episode change re-measures the boxes it just moved.
    "options/sub-font-size",
    "options/sub-color",
    "options/sub-outline-color",
    "options/sub-back-color",
    "options/sub-border-style",
    "options/sub-outline-size",
    "options/sub-shadow-offset",
    "options/sub-spacing",
    "options/sub-margin-x",
    "options/sub-margin-y",
    "options/sub-align-x",
    "options/sub-align-y",
    "options/sub-blur",
    "options/sub-bold",
    "options/sub-italic",
    "options/sub-justify",
    "options/video-crop",
    "options/video-rotate",
    "eof-reached",  # #100: rising edge drives auto-advance (only when advance_hook is installed)
)
# Which observations are geometry inputs, which retire the cue identity, and which render space
# they revise all live in saitenka/runtime/playback.py — the sole interpreter.
_SETTLE_TIMER = "subtitle:navigation-settle"
_GEOMETRY_REFRESH_TIMER = "subtitle:geometry-refresh"

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


# Popup view/panel classes live in app/popups.py; the _Nested alias is kept because the controller
# internals and the test-suite reference the old private name.
_Nested = PopupView


class SessionController:
    """Owns the reader loop (see module docstring): subtitle draw → hover hit-test → tooltip → mine."""

    @property
    def osd(self) -> tuple[int, int]:
        return self.screen.osd

    @osd.setter
    def osd(self, value: tuple[int, int]) -> None:
        self.screen.osd = value

    @property
    def tip(self) -> TooltipState:
        """Read-only compatibility projection of the tooltip feature's owned state."""
        return self.tooltip_controller.state

    def __init__(  # noqa: PLR0913, PLR0917 -- optional backend is the native boundary seam
        self,
        ipc: MpvIPC,
        scorer: Scorer | None = None,
        anki=None,
        mine_cfg=None,
        dict_set=None,
        options: ReaderOptions | None = None,
        renderer: SubtitleRenderer | NullRenderer | None = None,
        geometry_backend: GeometryBackend | None = None,
        profile: Profile | None = None,
        tokenizer_warm: Future[None] | None = None,
        tts_ok: bool | None = None,  # noqa: FBT001 -- tri-state capability snapshot
        runtime_submit=None,
        assembly: SessionAssembly | None = None,
        **legacy_kw,
    ):
        """``options`` is the canonical grouped-knobs object (see app/config.py; a new knob is one
        dataclass field). Legacy exploded kwargs (``mine_key=…``, ``tip_max_frac=…``) are still
        accepted and routed onto the groups; unknown names raise TypeError. ``renderer`` is the
        subtitle-draw strategy (app/subtitle_render.py) — pass ``NullRenderer()`` to run headless."""
        o = options or ReaderOptions()
        if legacy_kw:
            o = o.with_overrides(**legacy_kw)
        if assembly is None:
            from saitenka.app.session_assembly import build_session_assembly

            assembly = build_session_assembly(ipc, o, runtime_submit=runtime_submit)
        self._assembly = assembly
        self.options = o
        # Episode-lifetime state, addressed directly as ``episode.<field>`` — no shim stands in
        # front of it. A file change rebinds this in one move (#100 re-slot), which is what makes
        # the reset leak-free; see app/reader_context.py.
        self.episode = EpisodeContext()
        self.interaction = InteractionContext()  # hover/tooltip/reveal-scoped state
        self.ui_scale = max(0.75, min(2.0, float(o.panels.scale)))
        self.ipc = ipc
        self._interactive_ready = False
        self._connection = ConnectionStore(ipc)
        # Supplied by composition (`create_session_controller`), never probed off `ipc`: which egress the
        # overlay uses is a wiring decision, not something to infer from a collaborator's methods.
        self.ov = assembly.overlay
        self.lifecycle_surfaces = assembly.surfaces
        self.screen = assembly.screen
        self.help_controller = assembly.help
        # Hand teardown to the runtime at the point of construction, so the lifetime belongs to
        # whoever owns it rather than to a line in a teardown table far away. We keep *using* it;
        # what moves is when it closes. False means no runtime owns this session, and the close
        # table's fallback still has to run.
        # `getattr`, like the job-lane port below: a partial IPC (the benches' fake) constructs a
        # SessionController without implementing every runtime port, and construction must not demand one.

        self._runtime_owns_surfaces = ipc.register_session_resource(
            SURFACES_RESOURCE, self.lifecycle_surfaces
        ) and ipc.register_session_resource(OVERLAY_RESOURCE, self.ov)
        self.interaction_surfaces = InteractionSurfaces(self.ov)
        self.lifecycle_timers = LifecycleTimers(ipc)
        self._analysis_submit = analysis_overlay.configure_runtime_job(ipc)
        self._subtitle_fetch_submit = subtitle_modes.configure_runtime_job(ipc)
        self._subtitle_fetch_sequence = 0
        self._subtitle_force_select_revision = 0
        self._sub_picker_submit = sub_picker.configure_runtime_job(ipc)
        current_renderer: CurrentSubtitleRenderer = (
            renderer if renderer is not None else SubtitleRenderer()
        )
        self.native_geometry: native_subtitles.NativeSubtitleGeometry | None = None
        if o.subtitle_geometry.native_visible and renderer is None:
            current_renderer = NativeVisibleRenderer()
        # No provider is chosen here. Which implementation renders geometry is a composition
        # decision (`session_factory._geometry_backend`); a host that picks its own provider cannot
        # be handed a different one, which is what makes the fake/null/libass conformance contract
        # testable at all.
        self.subtitle_pipeline = SubtitleModeCoordinator(current_renderer, geometry_backend)
        if o.subtitle_geometry.native_visible:
            self.native_geometry = native_subtitles.NativeSubtitleGeometry(
                SubtitleGeometryWorker(
                    self.subtitle_pipeline,
                    cache_max=o.subtitle_geometry.cache_max,
                    submit=configure_geometry_lane(ipc),
                ),
                native_subtitles.GeometryPorts(
                    pipeline=self.subtitle_pipeline,
                    degrade=self._degrade_native_subtitle_geometry,
                    clear_interaction=self._clear_native_interaction,
                    use_native=self._use_native_subtitle_renderer,
                    ownership_undecided=self._native_ownership_undecided,
                    redraw=self.draw_subtitle,
                    reschedule=self._arm_geometry_refresh,
                    publish=self._publish_geometry,
                    tokenize_lookahead=lambda text: native_subtitles._lookahead_tokenized(
                        text,
                        normalise=cue_key,
                        coordinator=self._annotation if self._annotation_async else None,
                        annotation_key=self._annotation_key,
                        annotation_inputs=self._annotation_inputs,
                        tokenize=self._tokenize_cue,
                    ),
                ),
                lookahead=o.subtitle_geometry.lookahead,
                formats=native_subtitles.native_formats(o.subtitle_geometry.native_formats),
            )
            native_subtitles.connect_drift_sink(current_renderer, self.native_geometry)
        self.sub_size_override = o.tooltip.sub_size
        self.bottom_margin_frac = o.tooltip.bottom_margin_frac
        # Alpha (0–255) of the translucent box behind the rendered subtitle; 0 = no box (fully see-through).
        self.sub_bg_opacity = max(0, min(255, o.tooltip.sub_background_opacity))
        self.scorer = scorer  # app.scoring.Scorer | None — per-word coloring
        self.styles: list | None = None
        # Progressive startup: deps loaded on a background thread, injected on the main thread by the
        # poll loop (see load_deps_async / _apply_deps). Until then, subs render plain + a spinner shows.
        initial_profile_name = profile.name if profile is not None else "default"
        mining_identity = MiningIdentity(initial_profile_name, 0)
        self._loading = False
        self._load_frame = 0
        # The whole group, not a field per key: `active_bindings` resolves `key_attr` against it, and
        # a flat copy per key was a second representation of every one of them.
        self.keys = o.keys
        self.play_audio = o.mining.play_audio
        self.show_preview = o.mining.show_preview  # auto-pop the card-preview panel after a mine
        # Interactive sessions publish this optional subprocess probe later; deterministic
        # demo/screenshot assembly supplies it synchronously through SessionServices.
        self._tts_ok = bool(tts_ok)

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
        # subtitle navigation keys (configurable; defaults match SUB_NAV_DEFAULTS)
        self.tip_max_frac = o.tooltip.tip_max_frac  # BASE tooltip viewport ≤ this frac of the video
        self.nested_max_frac = o.tooltip.nested_max_frac  # nested (scan) popup viewport frac cap
        if o.tooltip.annotation_mode not in {"full", "hover"}:
            raise ValueError(f"unknown annotation mode: {o.tooltip.annotation_mode!r}")
        self.annotation_mode: subtitle_intents.AnnotationMode = o.tooltip.annotation_mode
        self.annotation_hover = False
        # Visual-only: draw the kanji panel's big headword in the numbered stroke-order font. Set here
        # (the shared SessionController init) so both the run and attach seams get it from one place; a pure render
        # flag threaded onto the kanji Entry, never gating what's looked up or the panel-cache identity.
        self.kanji_stroke_order = o.tooltip.kanji_stroke_order
        self.band_cache_max = o.tooltip.band_cache_max  # LRU cap on retained render bands per panel
        self.raw_band_ceiling = (
            o.tooltip.raw_band_ceiling_mb * 1024 * 1024
        )  # bytes; 0 = always compress
        # Session-lifetime state (app/reader_context.py SessionContext) — durable across every #100
        # episode re-slot: the #149 persistent render caches (opt-in, built lazily / use-when-available so
        # a non-dict or opted-out session never touches disk), the in-deck mined set, the Anki
        # reachability cache, and the review backlog store.
        self.session = SessionContext(
            RenderCacheState(
                cache_on=o.tooltip.render_cache,
                cache_max_bytes=o.tooltip.render_cache_max_mb * 1024 * 1024,
                cache_min_height_px=o.tooltip.render_cache_min_height,
                mask_atlas_on=o.tooltip.mask_atlas,  # persistent glyph mask atlas (#149 Tier-1), opt-out
            )
        )
        self._mask_atlas_startup = mask_atlas_startup.ActivationState()
        self._mask_atlas_submit = mask_atlas_startup.configure_runtime_job(ipc)
        # Idle crisp post-render (hi-dpi): after the instant soft upscale, a single background worker
        # re-renders the CURRENT viewport at NATIVE resolution (reusing a native-scale panel across scrolls
        # of the same word) and the poll loop swaps it in — so scrolling stays crisp, not just the first band.
        # One-panel crisp (scale-as-boundary): the ONE reference panel composites at the display scale
        # (native glyph masks over 1× geometry). ``crisp_upscale`` off → soft-only (never native).
        self._crisp_on = o.tooltip.crisp_upscale
        self._tip_scale_override = o.tooltip.tip_scale  # >0 fixes TipScale.display (see config)
        # Resolve the tooltip geometry backend ONCE (probes the optional taffylite wheel behind the
        # import chokepoint; missing → default). Threaded to every Panel.from_rows so all popups agree.
        self.layout_backend = resolve_backend(o.tooltip.layout_engine)
        self.layout_engine = backend_label(
            self.layout_backend
        )  # effective tag for logs + span attrs
        # Positive, truthful signal in the report bundle (overlay.log): the EFFECTIVE backend vs what was
        # requested — a "taffy" request landing on 'default' is a silent-fallback flag.
        log.info("layout backend: %s (requested %r)", self.layout_engine, o.tooltip.layout_engine)
        # background prefetch: render the paused line's tooltips ahead of the mouse. The worker does
        # CPU-only work (lookup + render + BGRA), NEVER touches the mpv IPC socket (main thread only).
        self.prefetch = o.prefetch
        self.prefetch_workers = (
            o.perf.prefetch_workers
        )  # constrained-parallel (GIL build) worker count
        self.prefetch_lookahead = (
            o.perf.prefetch_lookahead
        )  # cues to warm ahead of the current line
        # Head-prefetch (see config.py PerfOptions):
        # speculatively renders the SAME viewport-capped head a real hover would, via the SAME
        # panel_for()/panel_cache path, for a SELECTIVE subset of upcoming words (n+1/forgotten/
        # rare-frequency, excluding already-known/-mined) — no separate cache tier, so a later hover
        # is a plain panel_cache hit with no new key-matching logic to get wrong.
        self.head_prefetch_lookahead = o.perf.head_prefetch_lookahead
        # A dedicated broker lane keeps speculative work behind interactive raster work.
        self.prefetch_state = prefetch.PrefetchState(o.perf.head_prefetch_queue_max)
        self.tooltip_controller = tooltip_controller.TooltipController(
            ipc,
            self.engaged_build_ports,
            panel_cache_max=o.tooltip.panel_cache_max,
            pause_enabled=o.tooltip.pause_on_tooltip,
            delays=HoverDelays(
                scan=o.tooltip.scan_delay,
                hide=o.tooltip.hide_delay,
                switch=o.tooltip.hover_switch_delay,
            ),
            flash_seconds=o.tooltip.flash_secs,
            key_context=assembly.tooltip_keys,
        )
        self.interaction.tooltip = self.tooltip_controller
        # Per-cue tokenization cache (app/token_cache.py): source line → its tokenized+scored result,
        # so a looped/re-watched/nav-back line annotates at cue time with no plain-then-upgrade flicker.
        self.token_cache = TokenCache(o.perf.token_cache_max)
        # Interactive run/attach enables this lazily when async dependencies are wired. Keeping the
        # worker lazy avoids creating a thread for pure render/tests and the synchronous demo seam.
        self._annotation: cue_annotation.CueAnnotationCoordinator | None = None
        self._tokenizer_warm = tokenizer_warm
        self._annotation_executor = cue_annotation.AnnotationExecutor(tokenizer_warm)
        self._annotation_submit = cue_annotation.configure_runtime_job(
            ipc, self._annotation_executor
        )
        self._annotation_async = False
        self._dependencies_settled = True
        self._dependency_generation = 0
        self._current_cue_identity: cue_annotation.CueIdentity | None = None
        self._cue_retired = True
        self._cue_identity_ever_installed = False
        self._annotation_episode_index: CueIndex | None = None
        self._annotation_episode_cursor = 0
        self.profile_controller = ProfileController(
            profile,
            dict_set,
            ProfileInvalidation(
                invalidate_tokenizer=lambda: self._invalidate_profile_tokenizer(),
                invalidate_dictionary=lambda: self._invalidate_profile_dictionary(),
                reset_episode_warm=lambda: self._reset_profile_episode_warm(),
            ),
            ProfileSubtitles(
                current_subtitle_slang=lambda: self.subtitle_slang,
                has_subtitle_track=self._has_profile_subtitle_track,
                select_subtitle_track=lambda slang: self._select_profile_subtitle_track(slang),
                retokenize_current_cue=lambda: self._retokenize_current_cue(),
            ),
            ProfileAftermath(
                warm_episode=lambda: self.warm_episode_tokens(),
                notify=lambda text, kind: self.toast(text, kind),
            ),
        )
        self._mouse_in = False  # cursor over the video window — an engagement signal
        self._scrolled_this_tick = False  # a wheel/tip-scroll ran this poll tick — for render-span
        # attribution (did hover-driven scan/nested-popup work land in the same tick as a scroll?)
        self._runtime_announced = (
            False  # the runtime banner prints once, after prefetch actually starts
        )
        self._stop = threading.Event()
        # translation reveal: manual toggle (`t`), or auto-reveal on hover when opted in.
        # Auto keeps the anti-crutch spirit — the EN only appears while you're actively looking a
        # word up (a tooltip is shown), not for every line you already understand.
        self.auto_translate = o.translation.auto_translate
        self._sub_picker_lister: Callable[[str], tuple] | None = None
        self.analysis = analysis_overlay.AnalysisState()
        # Where the preview's last paint landed, plus the media the mine captured for it
        # (app/card_preview.py PreviewPanel). What is composed is the slice's.
        self.interaction.preview_panel = card_preview.PreviewPanel()
        self.mining_controller = self._assemble_mining_controller(mining_identity, anki, mine_cfg)
        self.profile_dependencies = reader_deps.ProfileDependencies(
            mining_identity,
            self._profile_dependency_apply,
        )
        # INTERACTION's claim on mpv's clicks and wheel, as a resource with a lifetime: the
        # runtime retires it at `PARTICIPANTS`, and an effect can only retire what it can find.

        self._mouse = mouse_capture.MouseCapture(
            ipc, self.lifecycle_timers, self._wants_mouse_capture
        )
        ipc.register_session_resource(INPUT_CAPTURE_RESOURCE, self._mouse)
        # The session's persistent writers, retired at `STORES`. Wrapped rather than registered
        # directly: the recorder is per-episode and both stores open lazily, so the resource has
        # to resolve them when it closes, not when it registers.

        for name, retire in (
            (SESSION_SUMMARY_RESOURCE, lambda: self._report_session(self.finish_session_stats())),
            (BACKLOG_RESOURCE, lambda: self._close_backlog_store()),
            (MINED_RESOURCE, lambda: self.mining_controller.close_store()),
        ):
            ipc.register_session_resource(name, session_resources.Retiring(retire))
        # The capability probes, the interaction work, and every worker lane — the close half's
        # three remaining phases. Named by the same tables the dispatcher orders them from, so a
        # step and its position cannot drift apart.

        self._close_participants: dict[str, Callable[[], object]] = {}
        #: Which close phases the runtime actually performed, filled by the announcement.
        self._runtime_close_phases: dict[ClosePhase, bool] = {}
        self._lane_deadline = 0.0
        for name in (
            CAPABILITY_PARTICIPANTS + INTERACTION_WORK_PARTICIPANTS + WORKER_LANE_PARTICIPANTS
        ):
            ipc.register_session_resource(
                name, session_resources.Retiring(self._participant_for(name))
            )
        # The setup steps, run phase by phase from `run`. Registered here so the runtime owns
        # *what* each phase does; the SessionController keeps only the order and the no-runtime fallback.

        self._startup_steps: dict[events.StartPhase, Callable[[], object]] = {
            events.StartPhase.PROCESS: self._guard_main_render,
            events.StartPhase.RENDER_SPACE: self.refresh_osd,
            events.StartPhase.OBSERVERS: self._start_observing_traced,
            events.StartPhase.INPUT: self._register_keybinds_traced,
            events.StartPhase.COLLABORATORS: self._seed_collaborators,
            events.StartPhase.HISTORY: self._open_session_history,
            events.StartPhase.DIAGNOSTICS: self._attach_diagnostics,
        }
        for name, step in (
            (RENDER_GUARD_PARTICIPANT, events.StartPhase.PROCESS),
            (RENDER_SPACE_PARTICIPANT, events.StartPhase.RENDER_SPACE),
            (OBSERVERS_PARTICIPANT, events.StartPhase.OBSERVERS),
            (INPUT_PARTICIPANT, events.StartPhase.INPUT),
            (COLLABORATORS_PARTICIPANT, events.StartPhase.COLLABORATORS),
            (HISTORY_PARTICIPANT, events.StartPhase.HISTORY),
            (DIAGNOSTICS_PARTICIPANT, events.StartPhase.DIAGNOSTICS),
        ):
            # Late-bound: the step reads collaborators this constructor has not finished building.
            ipc.register_session_resource(name, session_resources.Starting(self._step_for(step)))
        # The subtitle raster, retired at `RENDERING`. `native_geometry` is installed after this
        # point, so every one of these resolves it when it closes rather than now.
        # The two connection acts. Registered here with the rest and late-bound for the same
        # reason: both read collaborators this constructor has not finished building.
        ipc.register_session_resource(
            CUE_RETIRE_RESOURCE,
            session_resources.Retiring(lambda: self._retire_cue_identity("connection-lost")),
        )
        ipc.register_session_resource(
            SUBTITLE_REPLAY_PARTICIPANT,
            # Late-bound like every other registered step: an early-bound method also freezes the
            # seam a test replaces, and these two are reached only through the effect.
            session_resources.Starting(lambda: self._on_ipc_reconnect()),
        )
        ipc.register_session_resource(
            RESLOT_PARTICIPANT, session_resources.Starting(lambda: self._on_file_loaded())
        )
        ipc.register_session_resource(
            COMMAND_PERFORMER,
            session_resources.Performing(lambda effect: self._run_user_command(effect)),
        )
        ipc.register_session_resource(
            PLAYBACK_DELTAS_PERFORMER,
            session_resources.Performing(lambda effect: self._apply_playback_deltas(effect)),
        )
        for name, retire in (
            (
                SUBTITLE_DEACTIVATE_RESOURCE,
                lambda: self.subtitle_pipeline.deactivate(self.subtitle_target()),
            ),
            (SUBTITLE_CLEAR_RESOURCE, lambda: self._clear_subtitle_pixels()),
            (SUBTITLE_CLOSE_RESOURCE, lambda: self._close_subtitle_raster()),
        ):
            ipc.register_session_resource(name, session_resources.Retiring(retire))
        # Event-driven property state (observe_property); empty + off until run() calls
        # start_observing(), so direct get_property keeps working for tests / pre-run paths.
        self._observing = False
        # Sole interpreter of raw mpv observations (saitenka/runtime/playback.py): it owns the
        # latest values, the explicit source/track/render-space revisions, and the decision that a
        # given observation conflicts with the installed cue identity.
        reducer = PlaybackReducer()
        self._projection = reducer.projection
        self._playback_store = PlaybackStore(self.ipc, reducer=reducer)
        # `Owner.SUBTITLE`'s slice: which mpv track plays which role. Session-lived like the
        # playback one, and episode-safe because a re-slot always runs `configure_subtitle_mode`,
        # whose event resets the whole state.
        self._subtitle_tracks = SubtitleTrackStore(self.ipc)
        # Runtime and no-runtime Help stores share the assembly's registered reducer factory.
        self.interaction.help_store = self.help_controller.store
        # …and its third: the subtitle picker. Its drawn geometry stays beside the slice rather than
        # in it — one paint on one screen is not what a session-lived slot holds.
        self._picker_store = PickerStore(self.ipc)
        self.interaction.picker_store = self._picker_store
        self.interaction.picker_panel = sub_picker.PickerPanel()
        # …and its fourth: the sidebar, cut the same way — the slice decides, the panel remembers
        # what one paint put on screen.
        self._sidebar_store = SidebarStore(self.ipc)
        self.interaction.sidebar_store = self._sidebar_store
        self.interaction.sidebar_panel = sidebar.SidebarPanel()
        # …and its ninth: the mined-card preview. Its rects and the clip's live `Popen` stay on the
        # panel beside it — a reducer can hold neither one paint's geometry nor a process.
        self._preview_store = PreviewStore(self.ipc)
        self.interaction.preview_store = self._preview_store
        self.surface_router = surfaces.build_surface_router(
            self.help_controller,
            self.interaction,
        )
        # `Owner.PRESENTATION`'s slice: the translation reveal. Declarations only — the surface is
        # already drawn or already gone by the time one arrives.
        self.translation_store = TranslationStore(self.ipc)
        self._geometry_refresh = geometry_refresh.RefreshWindow()
        #: Latest cue identity observed this drain, reconciled once at the batch boundary.
        self._pending_cue: playback.ObservedCue | None = None
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
        self.osd = (1280, 720)
        # subtitle state (populated by set_subtitle; initialised for the live run() path)
        self._first_sub_logged = False  # gates the one-time "first subtitle drawn" info log
        self.sub_text = ""
        self.lines: list[list[Token]] = []
        self.tokens: list[Token] = []
        # Normalized source of a cue drawn PLAIN because its annotation can't complete yet (dicts
        # loading); reader_deps re-renders it annotated once deps land. None = drawn annotated.
        self._sub_pending: str | None = None
        self._annotation_degraded = False
        self._warmed_index: CueIndex | None = (
            None  # sub index whose cues the episode warm has run for
        )
        self.boxes: list = []
        self.sub_origin: tuple[int, int] = (0, 0)
        self._nudge_pending = (
            False  # a draw happened while paused → re-flush the OSD next tick (#8172)
        )
        self.commands = self._build_command_router()
        self.start_prefetch()

        mask_atlas_startup.request(
            self._mask_atlas_startup,
            mask_atlas_startup.MaskAtlasRequest(
                enabled=self.session.render_cache.mask_atlas_on,
                path=cache_dir() / "mask-atlas.sqlite",
            ),
            self._mask_atlas_submit,
            self._finish_mask_atlas_startup,
        )

    def hover_view(self) -> hover_snapshot.HoverView:
        """Read-only snapshot of the hover stack (nested popup / tooltip / pause / nav / scan) —
        the public seam tests observe instead of the private ``_nest`` / ``_tip_*`` fields (#43)."""
        hysteresis = self.tooltip_controller.hover_store.current.hysteresis
        return hover_snapshot.snapshot(
            self.tip.nest,
            hover_snapshot.TipView(
                state=self.tip.view.state,
                key=self.tip.view.key,
                rect=self.tip.view.rect,
                hide_pending=hysteresis.tip_hide_pending,
            ),
            paused=self.interaction.hover_pause.held,
            nav_idx=self.episode.nav_idx,
            scan_target=hysteresis.scan_target,
        )

    # scale subtitle/tooltip to the video size (the user usually watches 1080p)
    @property
    def renderer(self) -> CurrentSubtitleRenderer:
        return self.subtitle_pipeline.renderer

    @renderer.setter
    def renderer(self, renderer: CurrentSubtitleRenderer) -> None:
        self.subtitle_pipeline.renderer = renderer

    @property
    def sub_size(self) -> int:
        return self.sub_size_override or max(28, round(self.osd[1] * 0.05))

    @property
    def tip_scale(self) -> prefetch.TipScale:
        """The tooltip's reference geometry and its factor onto the live display, as one value.

        The tooltip is a VIDEO-OVERLAY element: it tracks the vertical viewport, not the app-chrome
        ``ui_scale``. ``[tooltip] tip_scale`` fixes the factor as a cosmetic preference.
        """
        return prefetch.tip_scale(
            self.osd[1], override=self._tip_scale_override, max_frac=self.tip_max_frac
        )

    @property
    def chrome_scale(self) -> float:
        """App-chrome (help / sidebar / stats) display scale: the user's ``ui_scale`` folded with the
        same ``osd_h / REF_H`` display factor the subtitle and tooltip already track, so the chrome
        grows on a hi-dpi / large OSD instead of staying at a flat ``ui_scale`` (which, at scale 1.2 on a
        2x Retina panel, drew help/sidebar/stats tiny beside the OSD-scaled tooltip). The factor is
        clamped to ``>= 1.0``: it only scales chrome UP for a taller-than-1080p OSD, never below the
        user's ``ui_scale`` baseline — so every OSD at or under 1080p (including the goldens pinned at
        1080p) is byte-identical, and a 2x panel gets ~2x chrome."""
        return self.ui_scale * max(1.0, self.osd[1] / prefetch.REF_H)

    @property
    def bottom_margin(self) -> int:
        return round(self.osd[1] * self.bottom_margin_frac)

    # --- mpv property helpers -----------------------------------------------------------------
    def _get(self, prop: str) -> object | None:
        """One property, un-narrowed. The transport promises a JSON value and nothing more.

        The three readers below are the narrowings that have a consumer; each answers `None` (or
        empty) for a shape mpv did not send, because "the property is unset" and "mpv sent something
        this caller cannot use" are the same fact to every one of them.
        """
        return self.ipc.query(prop)

    def text_property(self, prop: str) -> str | None:
        value = self._get(prop)
        return value if isinstance(value, str) else None

    def _get_number(self, prop: str) -> float | None:
        value = self._get(prop)
        return float(value) if isinstance(value, int | float) else None

    def _get_mapping(self, prop: str) -> dict:
        value = self._get(prop)
        return value if isinstance(value, dict) else {}

    def _get_sequence(self, prop: str) -> list:
        value = self._get(prop)
        return value if isinstance(value, list) else []

    def start_observing(self, *, connection_replaced: bool = False) -> None:
        """Register ``observe_property`` for the hot-path properties and seed their initial values
        with ONE get_property each. After this, the poll loop consumes buffered ``property-change``
        events instead of doing blocking round-trips every tick. Main-thread only (IPC)."""
        replies = self._register_observers()
        for name in OBSERVED_PROPS:
            reply = replies[name]
            data = reply.get("data")
            if connection_replaced:
                self._observe_property(name, data)
            else:
                self._reduce_playback(events.PropertySeeded(name, data))
            if name == "sub-text/ass-full" and self.native_geometry is not None:
                self.native_geometry.observe_ass_full_reply(reply)
        self._observing = True
        # Seeding goes through projection.seed, which discards the deltas it would publish, so the
        # cue already on screen at startup never produces a CueObservationChanged. Reconcile it once
        # by hand — otherwise the overlay stays blank until mpv's next sub-text change.
        self._reconcile_sub_text(str(self._playback.value("sub-text") or ""))
        # Seed values are the first sign the mpv→client read path works: a None osd-dimensions here
        # (with mpv clearly running) means get_property replies aren't coming back — the pipe read is
        # dead, so nothing will ever draw. Logged so it lands in overlay.log / report.
        osd = self._playback.value("osd-dimensions")
        log.info(
            "observing mpv props; seed osd-dimensions=%r sub-text=%r",
            osd,
            self._playback.value("sub-text"),
        )
        # Same reason `sub-text` is reconciled by hand above: the seed publishes no delta, so a
        # transient latched by the pre-observe blocking read (3642x2096 six ms before mpv settled on
        # 3024x1898) would stand for the session, and every hit box is laid out against it.
        self.refresh_osd()
        if osd is None:
            log.warning(
                "osd-dimensions seed is None — mpv isn't returning get_property replies (dead pipe / "
                "attached to a not-yet-ready mpv); the overlay won't draw until that recovers"
            )
        else:
            self._probe_display_sources("seed", osd if isinstance(osd, dict) else {})

    def _register_observers(self) -> dict[str, dict]:
        replies = register_observer_set(self.ipc, tuple(OBSERVED_PROPS))
        replies = {name: replies.get(name) or {"error": "unavailable"} for name in OBSERVED_PROPS}
        return replies

    @property
    def subtitle_tracks(self) -> subtitle_state.SubtitleTrackState:
        """`Owner.SUBTITLE`'s slice, read-only. Change it by declaring what you selected."""
        return self._subtitle_tracks.current

    @property
    def jp_sid(self) -> int | None:
        return self._subtitle_tracks.current.jp_sid

    @property
    def en_sid(self) -> int | None:
        return self._subtitle_tracks.current.en_sid

    @property
    def subtitle_language(self) -> subtitle_modes.Language:
        return self._subtitle_tracks.current.language

    @property
    def subtitle_slang(self) -> str:
        return self._subtitle_tracks.current.slang

    @property
    def _translation_secondary_sid(self) -> int | None:
        return self._subtitle_tracks.current.secondary_sid

    @property
    def _last_announced_sid(self) -> int | None:
        return self._subtitle_tracks.current.announced_sid

    @property
    def track_ports(self) -> subtitle_modes.TrackPorts:
        """The seam the whole track-selection family converted onto.

        Built per call rather than held: the acts are bound methods and the slice is read through
        `tracks`, so there is nothing here worth keeping alive between decisions.
        """
        return subtitle_modes.TrackPorts(
            ipc=self.ipc,
            get=self._get,
            toast=self.toast,
            tracks=lambda: self._subtitle_tracks.current,
            declare=self.declare_subtitle,
            invalidate=self.invalidate_analysis,
            translation_visible=self.translation_visible,
            drop_index=self._drop_sub_index,
            rebuild_index=self.rebuild_sub_index,
            sample_cue=self._sample_cue_text,
            clear_cue=lambda: self.set_subtitle(""),
            redraw_cue=lambda: self.set_subtitle(self.sub_text),
        )

    def _drop_sub_index(self) -> None:
        self.episode.sub_index = None

    def rebuild_sub_index(self) -> None:
        """Re-index whichever track mpv has selected. The one place the four facts are bound."""
        from saitenka.app.embedded_subs import build_sub_index_for_current_track

        build_sub_index_for_current_track(
            self.ipc, self._get, self.load_sub_index, self.native_geometry
        )

    def _sample_cue_text(self) -> str:
        return subtitle_modes._sample_cue_text(self.episode.sub_index, self.sub_text)

    def declare_subtitle(self, event: events.SubtitleEvent) -> subtitle_state.SubtitleTrackState:
        """Advance `Owner.SUBTITLE`'s slice by one declaration and hand back what it now holds.

        No setters on the properties above: a track selection is a decision with a writer, and an
        assignment hides which decision was made. The return value is the new state, because a
        caller that has just declared something usually needs to read it back in the same breath.
        """
        return self._subtitle_tracks.dispatch(event)

    @property
    def _playback(self) -> playback.PlaybackState:
        return self._playback_store.current.state

    @_playback.setter
    def _playback(self, state: playback.PlaybackState) -> None:
        self._playback_store.current = PlaybackSlice(state)

    def _publish_geometry(self, boxes: list, origin: tuple[int, int] | None = None) -> None:
        """Take the hit boxes the geometry owner produced. Ordering is the generation fence's."""
        self.boxes = boxes
        if origin is not None:
            self.sub_origin = origin

    def _geometry_observation(self) -> native_subtitles.GeometryObservation:
        """The facts the geometry owner decides from, per operation — they all move per cue."""
        return native_subtitles.GeometryObservation(
            prop=self.observed_property,
            osd=self.osd,
            text=self.sub_text,
            tokens=self.tokens,
            lines=self.lines,
            index=self.episode.sub_index,
            normalise=cue_key,
            nav_index=self.episode.nav_idx,
            cue_hint=self.episode.geometry_cue_hint,
            cue_revision=self.cue_revision,
            is_skippable=self.profile_controller.tokenizer.is_skippable,
        )

    def subtitle_target(self) -> SubtitleTarget:
        """What the subtitle renderers act on. Built per call — `native_geometry` is installed
        after construction, so a target cached on the SessionController would predate it.

        `draw_request` and `refresh` stay callables: the target outlives the observation, and the
        legacy stage needs the request built at stage time rather than at snapshot time.
        """
        geometry = self.native_geometry
        return SubtitleTarget(
            ipc=self.ipc,
            get=self._get,
            prop=self.observed_property,
            surfaces=self.lifecycle_surfaces,
            refresh=(
                (lambda: None)
                if geometry is None
                else (lambda: geometry.refresh(self._geometry_observation()))
            ),
            draw_request=self._draw_request,
            source=None if geometry is None else geometry.source_path,
            native_unsupported=geometry is not None and geometry.source_unsupported,
            legacy_forced=self.subtitle_pipeline.legacy_forced,
        )

    def toggle_legacy_renderer(self) -> bool:
        """Carry the intent to the coordinator, then redraw the cue under the renderer it chose."""
        geometry = self.native_geometry
        if geometry is not None:
            geometry.invalidate(live=True)
        forced = self.subtitle_pipeline.force_legacy(
            self.subtitle_target(), forced=not self.subtitle_pipeline.legacy_forced
        )
        self.set_subtitle(self.sub_text)
        return forced

    def _draw_request(self) -> DrawRequest:
        """Snapshot the host once per draw, so the values cannot drift apart mid-render.

        The ONE place in the draw path that reads the host; everything downstream of it is a value.
        Named rather than inlined at its callers: two copies of this snapshot that drift apart is
        precisely the bug `DrawRequest` was introduced to prevent.
        """
        return DrawRequest(
            text=self.sub_text,
            lines=self.lines,
            osd=self.osd,
            sub_size=self.sub_size,
            bg_opacity=self.sub_bg_opacity,
            bottom_margin=self.bottom_margin,
            secondary_role=self.subtitle_language == SECOND_LANG,
            upgrade_pending=self._sub_pending is not None,
            annotation_degraded=self._annotation_degraded,
            annotation_visible=subtitle_raster.annotation_visible(
                mode=self.annotation_mode, hover_annotation=self.annotation_hover
            ),
            hover=self.tooltip_controller.selected,
            hover_span=self.interaction.hovered_word_meta.span,
            styles=self.styles,
            boxes=self.boxes,
            paused=bool(self.observed_property("pause")),
        )

    def _reduce_playback(self, event: events.PlaybackEvent) -> None:
        """Advance `Owner.PLAYBACK`'s slice by one event and apply what that turn published.

        Empty for a routed session — the turn's deltas arrived through `ApplyPlaybackDeltas` before
        this returned. What is left here is the store that keeps its own slice.
        """
        for delta in self._playback_store.dispatch(event):
            self._apply_playback_delta(delta)

    def _apply_playback_deltas(self, effect: object) -> None:
        """Perform `ApplyPlaybackDeltas`: `Owner.PLAYBACK`'s outbox, delivered.

        The tuple is bound by the effect rather than read back off the slice, which is also what
        makes it safe to re-enter: applying one delta can reduce another event (`AuthoredCueStale`
        probes mpv and seeds the reply) and replace the slice underneath this loop.
        """
        assert isinstance(effect, ApplyPlaybackDeltas)
        for delta in effect.deltas:
            self._apply_playback_delta(delta)

    def observed_property(self, name: str) -> Any:
        """Latest value of a property: the observed (event-driven) state when observing, else a
        blocking get_property (tests / pre-run paths)."""
        if self._observing and self._playback.observes(name):
            return self._playback.value(name)
        return self._get(name)

    def _on_property_change(self, ev: dict) -> None:
        name = ev.get("name")
        if name:
            self._observe_property(str(name), ev.get("data"))

    def _observe_property(self, name: str, data: object) -> None:
        """Hand one ordered observation to the projection and apply the deltas it publishes."""
        if name == "pause" and data != self._playback.value("pause"):
            # Breadcrumb for the "overlay only updates on mouse move" report: while paused, mpv's
            # d3d11 flip-model VO won't re-present the window on an overlay-add (see the
            # --d3d11-flip=no launch mitigation). Correlate pause spans with overlay draws.
            log.debug("mpv pause -> %s", data)
        self._reduce_playback(events.PropertyObserved(name, data))

    def _probe_ass_full(self) -> None:
        """Resolve mpv's authored-ASS capability once per file. Driven by `AuthoredCueStale`, which
        the projection publishes on the same observation that invalidated the cached probe."""
        if self.native_geometry is None or not self._ass_full_probe_dirty:
            return
        if self.native_geometry.ass_full_capability.value == "unknown":
            reply = self.ipc.probe("sub-text/ass-full")
            self._reduce_playback(events.PropertySeeded("sub-text/ass-full", reply.get("data")))
            self.native_geometry.observe_ass_full_reply(reply)
        self._ass_full_probe_dirty = False

    def _apply_playback_delta(self, delta: playback.PlaybackDelta) -> None:
        if isinstance(delta, playback.CueIdentityRetired):
            self._retire_cue_identity(delta.reason.value)
        elif isinstance(delta, playback.AuthoredCueStale):
            self._probe_ass_full()
        elif isinstance(delta, playback.CueObservationChanged):
            self._pending_cue = delta.cue  # coalesced at the drain boundary; latest wins
        elif isinstance(delta, playback.SubtitleSelectionChanged):
            self.retire_geometry_refresh()  # the track it was armed for is gone
            if self.native_geometry is not None:
                self.native_geometry.set_source(None, live=True)
            else:
                self.subtitle_pipeline.invalidate()
            subtitle_modes.on_primary_changed(self.track_ports, delta.sid)
        elif isinstance(delta, playback.SubtitleTimingChanged):
            if self.native_geometry is not None:
                self.native_geometry.record_clock_change(self.observed_property)
        elif isinstance(delta, playback.GeometryInputChanged) and self.native_geometry is not None:
            self._arm_geometry_refresh()
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
            # Watch time is accrued at the transition, not sampled by a tick: the segment that
            # just ended is exactly what the change delimits, and an idle runtime does no work.
            session_stats.accrue(
                self.episode.session_recorder,
                paused=bool(self.observed_property("pause")),
                language=self.subtitle_language,
            )
        elif isinstance(delta, playback.PointerMoved):
            # Hover reacts to the pointer moving, not to a tick noticing that it did. The dwells it
            # arms are deadlines, so a cursor that stops still gets its linger — which is why this
            # could not move until they were.
            self._update_hover()

    def _install_cue_identity(self, identity: cue_annotation.CueIdentity) -> None:
        """Bind the cue identity in both owners: the annotation state and the projection, which
        decides which later observation conflicts with it."""
        self._current_cue_identity = identity
        self._cue_retired = False
        self._reduce_playback(
            events.CueIdentityInstalled(identity.observed_start, identity.observed_end)
        )

    # --- coalesced geometry refresh ----------------------------------------------------
    def _arm_geometry_refresh(self) -> None:
        """Defer the refresh to a zero-delay deadline so one batch of input changes runs libass
        once, at the head of the next drain — after the whole batch has been observed."""
        generation = self.subtitle_pipeline.generation
        window, due = self._geometry_refresh.arm(generation)
        self._geometry_refresh = window
        if due is None:  # an armed deadline already covers this change
            return

        def fired(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._geometry_refresh_due(due)

        if not self.ipc.schedule_runtime_timer(
            owner=Owner.SUBTITLE,
            identity=due,
            timer=_GEOMETRY_REFRESH_TIMER,
            due_at=time.monotonic(),
            on_finished=fired,
        ):
            # Coalescing is an optimisation, not a guard: with no timer port (or a full one) the
            # refresh still has to happen, so run it now. Unlike the settle window — whose absence
            # must fail closed because it changes what the user sees — skipping this one would
            # silently drop hit boxes.
            self._geometry_refresh = self._geometry_refresh.retire()
            self._refresh_geometry()

    def _geometry_refresh_due(self, due: geometry_refresh.GeometryRefreshDue) -> None:
        if not self._geometry_refresh.fires(due, self.subtitle_pipeline.generation):
            return  # superseded, or the source moved under it
        self._geometry_refresh = self._geometry_refresh.retire()
        self._refresh_geometry()

    def _refresh_geometry(self) -> None:
        if self.native_geometry is not None:
            self.native_geometry.refresh(self._geometry_observation())

    def retire_geometry_refresh(self) -> None:
        """Drop a pending refresh; the source or track it was armed for is gone."""
        if self._geometry_refresh.armed is None:
            return
        self._geometry_refresh = self._geometry_refresh.retire()
        self.ipc.cancel_runtime_timer(_GEOMETRY_REFRESH_TIMER)

    # --- subtitle navigation settle window --------------------------------------------
    def open_settle_window(self) -> None:
        """Absorb mpv's mid-seek transients until the seek lands or the named deadline is due."""
        window = self.episode.sub_settle.begin()
        self.episode.sub_settle = window
        identity = window.identity

        def due(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._settle_due(identity)

        if not self.ipc.schedule_runtime_timer(
            owner=Owner.SUBTITLE,
            identity=identity,
            timer=_SETTLE_TIMER,
            due_at=time.monotonic() + subnav_settle.SETTLE_SECONDS,
            on_finished=due,
        ):
            # No gateway behind the port (tests, pre-run), or a full timer heap: either way, never
            # open a window we cannot retire. The port answers False for both.
            self.episode.sub_settle = window.retire()

    def _settle_due(self, identity: subnav_settle.NavigationSettleDue) -> None:
        self.episode.sub_settle = self.episode.sub_settle.due(identity)

    def retire_settle_window(self) -> None:
        """Close the window and cancel its deadline; safe to call when none is open."""
        if not self.episode.sub_settle.open:
            return
        self.episode.sub_settle = self.episode.sub_settle.retire()
        self.ipc.cancel_runtime_timer(_SETTLE_TIMER)

    def _replace_subtitle_source(self, path: object = None, *, reason: str) -> None:
        self.retire_settle_window()
        """A new authored subtitle source is live: revise it in the projection (which every cue
        identity is derived from) and retire the identity the old source produced."""
        self._reduce_playback(events.SourceReplaced(path))
        self._retire_cue_identity(reason)

    def _clear_cue_identity(self) -> None:
        """Drop the installed identity in both owners; the projection then stops treating later
        sub-start/sub-end observations as conflicts."""
        self._cue_retired = True
        self._current_cue_identity = None
        self._reduce_playback(events.CueIdentityRetireRequested(playback.RetireReason.CUE_TEXT))

    def _retire_cue_identity(self, reason: str) -> None:
        if self._cue_retired:
            self._clear_cue_identity()
            return
        log.debug("cue interaction retired: %s", reason)
        self._clear_cue_identity()
        self.teardown_tip()
        self.tooltip_controller.retire_selection()
        self.lines, self.tokens, self.styles, self.boxes = [], [], None, []

    def refresh_osd(self) -> bool:
        d = self.observed_property("osd-dimensions") or {}
        w, h = int(d.get("w") or self.osd[0]), int(d.get("h") or self.osd[1])
        if (w, h) != self.osd and w > 0 and h > 0:
            self.osd = (w, h)
            if self.native_geometry is None:
                self.subtitle_pipeline.invalidate()
            self._probe_display_sources("osd-change", d)
            return True
        return False

    def _probe_display_sources(self, reason: str, osd: dict) -> None:
        """Snapshot EVERY mpv size/scale source at an osd-dimensions change, so a report pinpoints WHICH
        one makes the tooltip scale (osd_h/REF_H) jitter — e.g. on retina the OSD backing-pixel height
        wobbles a few px while ``display-hidpi-scale`` stays a clean 2.0 (→ key scale off the stable one).
        Emits a low-cardinality ``osd_probe`` span (trace_report breaks down each source's distinct values)
        + a full-fidelity log line. Cheap: only fires on an actual osd change (minutes apart in practice)."""
        probe = {p: self._get(p) for p in _DISPLAY_PROBE_PROPS}
        vop = probe.get("video-out-params")
        vop = vop if isinstance(vop, dict) else {}
        span_attrs = {
            "reason": reason,
            "tip_scale": f"{self.tip_scale.display:.4f}",
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

    # --- subtitle -----------------------------------------------------------------------------
    def teardown_tip(self) -> None:
        """Tear down the hover stack unconditionally: hide TIP_ID/NESTED_ID, reset all tooltip
        state, and release any pause a tooltip took. Called by `retire_hover` AND `set_subtitle`, so
        a cue change while a tooltip is showing always clears it via the real path — unconditional
        because `retire_hover` early-returns when hover is already -1, which a tip still on screen
        does not imply."""
        self.tooltip_controller.cancel_jobs()
        hide = getattr(self.ov, "hide_interactive", self.ov.hide)
        hide(TIP_ID)
        self._hide_nested()
        self._unbind_tip_keys()
        self.tooltip_controller.retire_state()
        self.resume_after_hover_pause()
        self._sync_auto_translation()

    def set_subtitle(
        self,
        text: str,
        *,
        revise_session_cue: bool = False,
        provisional_navigation: bool = False,
    ) -> None:
        if provisional_navigation:
            self.episode.nav_provisional_cue_counted = False
        # Per-cue breadcrumb (low frequency): correlates mpv's sub-text change with the overlay draw +
        # paused-state in the report — the mpv-log-vs-overlay-log gap the paused-OSD bug lives in.
        log.debug(
            "sub-text change: %d chars, paused=%s",
            len(text.strip()),
            self.observed_property("pause"),
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
        self.subtitle_pipeline.invalidate()
        self.subtitle_pipeline.cue_changed(self.subtitle_target(), nonempty=bool(text.strip()))
        # Tear down the hover stack via the shared path BEFORE mutating sub_text/hover so that
        # TIP_ID/NESTED_ID are hidden, _tip_rect/_tip_state/_tip_key/_nest are reset, and any
        # any tooltip-owned pause is released. `retire_hover` will not do: it early-returns on
        # hover already -1, which does not imply the tip is down (`_show_tooltip` can be called
        # without a hover).
        with otel_metrics.traced("teardown_tip"):
            self.teardown_tip()
        self.tooltip_controller.retire_selection()
        self.annotation_hover = False
        self.sub_text = text
        # Invariant 13: the projection owns which cue is current, so a SessionController-side decision about
        # it has to reach the projection too — otherwise the next changed cue fact reconciles mpv's
        # stale text back over this one.
        self._reduce_playback(events.CueTextReplaced(text))
        self._clear_cue_identity()
        self._sub_pending = None  # any cue change abandons a still-pending upgrade for the old cue
        self._annotation_degraded = False
        self.episode.nav_idx = (
            -1
        )  # any external cause of a cue change invalidates the nav chaining hint
        with otel_metrics.traced("hide_preview"):
            self._hide_preview()  # a new cue → dismiss the last card preview
        if not text.strip():
            self.lines, self.tokens, self.boxes = [], [], []
            if self.native_geometry is not None:
                self.native_geometry.mark_empty()
            self.subtitle_pipeline.clear(self.lifecycle_surfaces, self.ipc)
            hide = getattr(self.ov, "hide_interactive", self.ov.hide)
            hide(TIP_ID)
            return
        self._record_session_cue(
            text,
            revise=revise_session_cue,
            provisional_navigation=provisional_navigation,
        )
        if self.subtitle_language == SECOND_LANG:
            self.lines, self.tokens, self.styles = [], [], None
            self.boxes = []
            self._install_cue_identity(self._annotation_identity(cue_key(text)))
            self._cue_identity_ever_installed = True
            self.draw_subtitle()
            return
        # honour explicit line breaks (\n, ASS \N); tokenize each source line separately
        norm = cue_key(text)
        cached = self.token_cache.get(norm)
        self._install_cue_identity(self._annotation_identity(norm))
        self._cue_identity_ever_installed = True
        if cached is not None:
            self._apply_tokenized_cue(cached)
        elif self._annotation_async:
            self.lines, self.tokens, self.styles, self.boxes = [], [], None, []
            self._sub_pending = norm
            if self._dependencies_settled:
                self._schedule_current_annotation(norm)
            self.draw_subtitle()
            return
        else:
            self._apply_tokenized_cue(self._tokenize_cue(norm))
        # A cue must appear at its cue time even when annotation isn't ready. While the dictionaries
        # are still loading the tokenization can't be complete (no compound merge, no coloring), so
        # draw the cue PLAIN now; reader_deps re-renders it in place once deps land. A cache hit or a
        # deps-ready miss tokenizes synchronously (fast) and annotates immediately.
        self._sub_pending = norm if self.profile_controller.dict_set is None else None
        if self.native_geometry is not None:
            self.boxes = []
            self.native_geometry.schedule(self._geometry_observation())
        self.draw_subtitle()

    def _record_session_cue(self, text: str, *, revise: bool, provisional_navigation: bool) -> None:
        recorder = self.episode.session_recorder
        if recorder is None:
            return
        identity = (
            self.subtitle_language,
            self.observed_property("sub-start"),
            self.observed_property("sub-end"),
            text,
        )
        if revise:
            recorder.revise_cue(identity)
            return
        counted = recorder.record_cue(identity)
        if provisional_navigation:
            self.episode.nav_provisional_cue_counted = counted

    def _enable_async_annotation(self) -> None:
        self._annotation_async = True
        self._dependencies_settled = False
        if self._annotation is None:
            self._annotation = cue_annotation.CueAnnotationCoordinator(
                cache_max=self.options.perf.token_cache_max,
                executor=self._annotation_executor,
                submitter=self._annotation_submit,
                on_result=self._finish_annotation,
            )

    def prepare_subtitle_blocking(self, text: str) -> None:
        """Prepare a demo/screenshot cue through the annotation worker before capture."""
        self._annotation_async = True
        self._dependencies_settled = True
        if self._annotation is None:
            self._annotation = cue_annotation.CueAnnotationCoordinator(
                cache_max=self.options.perf.token_cache_max,
                executor=self._annotation_executor,
                submitter=self._annotation_submit,
                on_result=self._finish_annotation,
            )
        norm = cue_key(text)
        cue = self._annotation.resolve(
            self._annotation_key(norm),
            self._annotation_inputs(norm),
            priority=cue_annotation.AnnotationPriority.CURRENT,
            drive=self._drive_annotation_once,
        )
        self.token_cache.put(norm, cue)
        self.set_subtitle(text)

    def _drive_annotation_once(self, timeout: float | None) -> None:
        """A turn taken from inside cue construction, so it settles nothing: the reconcile this is
        nested in owns the batch boundary, and running a second one here would build the cue again
        against the half-updated identity the outer one is still assembling."""
        self.ipc.receive_session(timeout, self._drain_event)

    def _annotation_identity(self, norm: str) -> cue_annotation.CueIdentity:
        return cue_annotation.CueIdentity(
            self._playback.media.source.value,
            self.observed_property("sid"),
            self.subtitle_language,
            norm,
            self.observed_property("sub-start"),
            self.observed_property("sub-end"),
            self.episode.nav_idx if self.episode.nav_idx >= 0 else None,
        )

    def _annotation_key(self, norm: str) -> cue_annotation.AnnotationWorkKey:
        identity = self._annotation_identity(norm)
        return cue_annotation.AnnotationWorkKey(
            norm,
            identity.source_epoch,
            identity.track_identity,
            identity.subtitle_role,
            self.token_cache.generation,
            self._dependency_generation,
        )

    def _annotation_inputs(self, norm: str) -> cue_annotation.AnnotationInputs:
        return cue_annotation.AnnotationInputs(
            norm,
            self.profile_controller.tokenizer,
            getattr(self.profile_controller.dict_set, "terms_exist", None),
            self.scorer,
            len(getattr(self.profile_controller.dict_set, "dicts", ())),
        )

    def _schedule_current_annotation(self, norm: str) -> None:
        if self._annotation is None or self.subtitle_language == SECOND_LANG:
            return
        identity = self._annotation_identity(norm)
        self._install_cue_identity(identity)
        cached = self._annotation.submit(
            self._annotation_key(norm),
            self._annotation_inputs(norm),
            priority=cue_annotation.AnnotationPriority.CURRENT,
            waiter=identity,
        )
        if cached is not None:
            self._publish_annotation(cached, identity)

    def _dependencies_changed(self) -> None:
        self._dependency_generation += 1
        self._dependencies_settled = True
        self._annotation_degraded = False
        self.token_cache.clear()
        if not self.sub_text.strip() or self.subtitle_language == SECOND_LANG:
            return
        self.teardown_tip()
        self.tooltip_controller.retire_selection()
        self.lines, self.tokens, self.styles, self.boxes = [], [], None, []
        norm = cue_key(self.sub_text)
        self._sub_pending = norm
        if self.native_geometry is not None:
            self.native_geometry.invalidate(live=True)
        self._schedule_current_annotation(norm)
        self.draw_subtitle()

    def _publish_annotation(self, cue: TokenizedCue, identity: cue_annotation.CueIdentity) -> bool:
        if (
            self._annotation_disposition_for(identity, cue)
            is not cue_annotation.AnnotationDisposition.PUBLISH
        ):
            return False
        self.token_cache.put(identity.normalized_text, cue)
        self._apply_tokenized_cue(cue)
        self._sub_pending = None
        self._annotation_degraded = False
        if self.native_geometry is not None:
            self.native_geometry.schedule(self._geometry_observation())
        self.draw_subtitle()
        return True

    def _annotation_disposition_for(
        self, identity: cue_annotation.CueIdentity, cue: TokenizedCue | None
    ) -> cue_annotation.AnnotationDisposition:
        return self._annotation_disposition(
            cue_annotation.AnnotationResult(
                self._annotation_key(identity.normalized_text), identity, cue, None, 0.0, 0.0
            )
        )

    def _annotation_disposition(
        self, result: cue_annotation.AnnotationResult
    ) -> cue_annotation.AnnotationDisposition:
        current_key = (
            self._annotation_key(result.identity.normalized_text)
            if result.identity is not None
            else None
        )
        return cue_annotation.disposition(
            result,
            current_identity=self._current_cue_identity,
            current_key=current_key,
            cue_retired=self._cue_retired,
            pending_text=self._sub_pending,
        )

    def _finish_annotation(self, result: cue_annotation.AnnotationResult) -> None:
        with otel_metrics.traced("cue_annotation", phase="publish") as span:
            span.set("queue_wait_ms", round(result.queue_wait_ms, 3))
            span.set("work_ms", round(result.work_ms, 3))
            outcome = self._annotation_disposition(result)
            if outcome is cue_annotation.AnnotationDisposition.DEGRADE:
                # The cue is still on screen: drop the pending upgrade and keep its plain pixels.
                self._sub_pending = None
                self._annotation_degraded = True
                log.warning("cue annotation unavailable; keeping plain subtitles")
            if outcome.failed:
                span.set("outcome", "failed")
                span.set("failure", "annotation-error")
                return
            if outcome is cue_annotation.AnnotationDisposition.PUBLISH:
                assert result.cue is not None and result.identity is not None
                self._publish_annotation(result.cue, result.identity)
            span.set("outcome", outcome.value)

    def warm_episode_tokens(self) -> None:
        """Kick off the background full-episode token warm (no-op without prefetch + a dict + index)."""
        prefetch.warm_episode_tokens(self.warm_ports)

    def _start_episode_annotation(self, index: CueIndex) -> None:
        self._annotation_episode_index = index
        self._annotation_episode_cursor = 0
        self._feed_episode_annotation()

    def _feed_episode_annotation(self) -> None:
        coordinator = self._annotation
        index = self._annotation_episode_index
        if coordinator is None or index is None or self.episode.sub_index is not index:
            return
        while coordinator.pending_count() < 4 and self._annotation_episode_cursor < len(index.cues):
            cue = index.cues[self._annotation_episode_cursor]
            self._annotation_episode_cursor += 1
            norm = cue_key(cue.text)
            coordinator.submit(
                self._annotation_key(norm),
                self._annotation_inputs(norm),
                priority=cue_annotation.AnnotationPriority.EPISODE,
            )

    def _tokenize_cue(self, norm: str, *, generation: int | None = None) -> TokenizedCue:
        """Tokenize + compound-merge + score one normalized cue into a :class:`TokenizedCue`, memoizing
        a COMPLETE, non-empty result (see TokenCache.put) so a repeated line is a hit. Pure of overlay
        state, so a cache hit reproduces it exactly. ``generation`` (the background episode-warm passes
        its captured value) gates the store: a profile swap that cleared the cache mid-warm drops it."""
        # Dictionary-attested compound merge (応急+処置 → 応急処置) — one hover/color/mine unit like
        # Yomitan. Optional dict capability, absent until the dicts finish loading (like has_term).
        exists = getattr(self.profile_controller.dict_set, "terms_exist", None)
        with otel_metrics.traced("tokenize_line", chars=str(len(norm))):
            raw = (
                self.profile_controller.tokenizer.tokenize(ln)
                for ln in norm.split("\n")
                if ln.strip()
            )
            lines = [
                self.profile_controller.tokenizer.merge_dict_compounds(t, exists) if exists else t
                for t in raw
            ]
        tokens = [t for line in lines for t in line]
        # score the whole cue (N+1 splits by sentence punctuation across lines); warms lookup cache
        with otel_metrics.traced("score_line"):
            styles = self.scorer.score_line(tokens) if self.scorer else None
        cue = TokenizedCue(lines, tokens, styles)
        # Only memoize a complete annotation — a pre-deps tokenization (no compound-merge dict) is a
        # transient that must re-attempt on the next identical line once the dicts load.
        self.token_cache.put(norm, cue, complete=exists is not None, generation=generation)
        return cue

    def _apply_tokenized_cue(self, cue: TokenizedCue) -> None:
        self.lines, self.tokens, self.styles = cue.lines, cue.tokens, cue.styles

    def _invalidate_profile_tokenizer(self) -> None:
        if self.native_geometry is not None:
            self.native_geometry.invalidate(live=True)
        else:
            self.subtitle_pipeline.invalidate()
        self.token_cache.clear()

    def _invalidate_profile_dictionary(self) -> None:
        self.session.render_cache.config_sig = None

    def _reset_profile_episode_warm(self) -> None:
        self._warmed_index = None

    def _has_profile_subtitle_track(self, slang: str) -> bool:
        return subtitle_modes.has_track_for_slang(self.ipc, slang)

    def _select_profile_subtitle_track(self, new_slang: str) -> None:
        startup = subtitle_modes.select_initial(self.ipc, new_slang)
        self.configure_subtitle_mode(startup, slang=new_slang)
        self.episode.sub_index = None
        self.rebuild_sub_index()

    def _retokenize_current_cue(self) -> None:
        """Re-render the on-screen cue under the freshly-swapped tokenizer — set_subtitle's tokenize
        path without its teardown/recording side effects. No-op when nothing's shown or the secondary
        (English) track is up (which never tokenizes)."""
        if not self.sub_text.strip() or self.subtitle_language == SECOND_LANG:
            return
        if self._annotation_async:
            self._retire_cue_identity("profile")
            norm = cue_key(self.sub_text)
            self._install_cue_identity(self._annotation_identity(norm))
            self._sub_pending = norm
            self._annotation_degraded = False
            self._schedule_current_annotation(norm)
            self.draw_subtitle()
            return
        self._apply_tokenized_cue(self._tokenize_cue(cue_key(self.sub_text)))
        if self.native_geometry is not None:
            self.native_geometry.refresh(self._geometry_observation())
        self.draw_subtitle()

    def draw_subtitle(self) -> None:
        result = self.subtitle_pipeline.draw_current(self.subtitle_target())
        if result is not None:
            # The write-back, here rather than inside the stage: the boxes and origin belong to the
            # cue that produced them, and this is the one place that owns them.
            self.boxes = result.boxes
            self.sub_origin = result.origin
            self._first_sub_logged = self.subtitle_pipeline.renderer.logged_first
        if self.native_geometry is not None:
            self.native_geometry.sync_pixel_owner(self.subtitle_pipeline.renderer)

    def _clear_native_interaction(self) -> None:
        self.teardown_tip()
        self.tooltip_controller.retire_selection()
        self.boxes = []
        self.subtitle_pipeline.clear(self.lifecycle_surfaces, self.ipc)

    def _degrade_native_subtitle_geometry(self) -> None:
        renderer = self.subtitle_pipeline.renderer
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", None)
        if owner != "legacy":
            self.boxes = []
        self.subtitle_pipeline.geometry_degraded(self.subtitle_target())

    def _use_native_subtitle_renderer(self) -> bool:
        return self.subtitle_pipeline.renderer.use_native(self.subtitle_target())

    def _native_ownership_undecided(self) -> bool:
        """True while a visibility assertion is in flight, so `_use_native_subtitle_renderer` said
        no for lack of an answer rather than because mpv refused. Publishing must wait, not degrade:
        the assertion's terminal re-drives the refresh."""
        renderer = self.subtitle_pipeline.renderer
        return isinstance(renderer, NativeVisibleRenderer) and renderer.assertion_in_flight

    # --- hover --------------------------------------------------------------------------------
    def _hit(self, mx: float, my: float) -> int:
        return subtitles.token_at(
            self.boxes,
            (mx, my),
            self.sub_origin,
            is_skippable=lambda index: self.profile_controller.tokenizer.is_skippable(
                self.tokens[index]
            ),
        )

    @staticmethod
    def _in_rect(rect, x: float, y: float) -> bool:
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def _update_hover(self) -> None:
        if not getattr(self.ov, "visible", True) or self.surface_router.suppress_hover(
            self.hover_suppression
        ):
            return
        tooltip.update_hover(self.tip_ports, self.hover_actions, self.hover_inputs)

    def set_hover(self, index: int) -> None:
        tooltip.set_hover(
            self.tip_ports,
            self.panel_ports,
            self.word_lookup,
            self.hover_inputs,
            self.show_actions,
            index,
        )

    def retire_hover(self) -> None:
        """Publish that nothing is hovered — the teardown half of the old `set_hover(-1)`."""
        tooltip.retire_hover(self.tip_ports, self.hover_inputs, self.show_actions)

    def prepare_hover_blocking(self, index: int) -> None:
        """Build the deterministic demo/screenshot hover before the event loop starts."""
        with self.tooltip_controller.blocking():
            tooltip.set_hover(
                self.tip_ports,
                self.panel_ports,
                self.word_lookup,
                self.hover_inputs,
                self.show_actions,
                index,
            )

    def set_annotation_hover(self, *, revealed: bool) -> None:
        target = bool(
            revealed
            and self.annotation_mode == "hover"
            and self.subtitle_language == MAIN_LANG
            and self.tokens
        )
        if target == self.annotation_hover:
            return
        self.annotation_hover = target
        self.draw_subtitle()

    def speak_hovered(self) -> None:
        self._stateless.run(hover_intents.HoverCommand.SPEAK)

    def copy_hovered(self) -> None:
        self._stateless.run(hover_intents.HoverCommand.COPY)

    def copy_token(self, t) -> None:
        tooltip.copy_token(self.toast, t)

    def copy_line(self) -> None:
        """Shift+C — copy the whole subtitle cue under the cursor (all its lines)."""
        self._stateless.run(subtitle_intents.SubtitleCommand.COPY_LINE)

    def copy_click(self) -> None:
        tooltip.copy_click(self.tip_ports, self.click_ports, self.hover_inputs)

    def on_click(self) -> None:
        if not self.ov.visible:
            return
        mp = self._get_mapping("mouse-pos")
        self.surface_router.route_click(self.click_target, mp.get("x", -1), mp.get("y", -1))

    def _panel_key(
        self,
        tok,
        inflected,
        *,
        mined: bool = False,
        phrase: tuple[str, ...] = (),
        group_mined: tuple[bool, ...] | None = None,
    ) -> tooltip_panel.PanelKey:
        return tooltip_panel.panel_key(
            self.panel_ports,
            tok,
            inflected,
            mined=mined,
            phrase=phrase,
            group_mined=group_mined,
        )

    @property
    def panel_ports(self) -> tooltip_panel.PanelPorts:
        """A panel build's per-turn inputs. `panel_style` is the half that does not change.

        Built per call so the mined set and the scroll flag are both read fresh: a panel keyed on a
        stale mined set shows the wrong header for a word that was mined since.
        """
        return tooltip_panel.PanelPorts(
            style=self.panel_style,
            mined_set=self.mining_controller.index_snapshot(),
            during_scroll=self._scrolled_this_tick,
            cache=self.tip.panel_cache,
            cap=self.tip_scale.cap,
        )

    def _is_mined(self, tok) -> bool:
        return tooltip_panel.is_mined(tok, self.mining_controller.index_snapshot())

    @property
    def sidebar_view(self) -> sidebar_module.SidebarView:
        """What the sidebar draws from, as one member rather than the fifteen it gathers.

        Every consumer takes the value now, so the host is read here and nowhere else in the chain
        — the precondition for the surface owning its own state.
        """
        return sidebar_module.SidebarView(
            store=self._sidebar_store,
            panel=self.interaction.sidebar_panel,
            active=sidebar_module._active_index(
                self.episode.sub_index,
                self.sub_text,
                sub_start=self._get("sub-start"),
                time_pos=self._get("time-pos"),
                preferred=self.episode.nav_idx,
            ),
            index=self.episode.sub_index,
            language=self.subtitle_language,
            osd=self.osd,
            chrome_scale=self.chrome_scale,
            surfaces=self.lifecycle_surfaces,
            video=self.text_property("path"),
            backlog=lambda: sidebar_module._ensure_store(self.session),
            mined=lambda: self.mining_controller.store,
            mined_exists=self.mining_controller.store_exists,
            backlog_exists=self.session.backlog_store is not None or backlog.db_path().exists(),
            scorer=self.scorer,
            tokenizer=self.profile_controller.tokenizer,
            analysis=self.analysis.current,
            can_mine=self.mining_controller.configured,
        )

    @property
    def sidebar_actions(self) -> sidebar_module.SidebarActions:
        """The sidebar's click acts, bound. Paired with `sidebar_view`."""
        return sidebar_module.SidebarActions(
            seek=lambda name, at: send_correlated(
                self.ipc, name, "set_property", "time-pos", at, owner=Owner.PLAYBACK
            ),
            bookmark=self.toggle_bookmark,
            mine=lambda: self._stateless.run(mine_intents.MineCommand.WORD),
            open_mined=lambda note_id: sidebar_module._open_mined(
                self.sidebar_view,
                self.sidebar_actions,
                self.preview_ports,
                self.card_source,
                note_id,
            ),
        )

    @property
    def hover_suppression(self) -> surfaces.HoverSuppression:
        """What a surface needs to decide whether it swallows the hover under the cursor.

        Named rather than inlined at the one call site: a hook's own unit test needs to build the
        same value production does, and a test that assembles the port by hand is a second
        definition of it that drifts from this one. Same for its two twins below.
        """
        return surfaces.HoverSuppression(
            self.interaction,
            self.observed_property("mouse-pos"),
            self.retire_hover,
            lambda: self.set_annotation_hover(revealed=False),
        )

    @property
    def wheel_step(self) -> surfaces.WheelStep:
        """What a surface needs to decide whether it claims a coalesced wheel step."""
        return surfaces.WheelStep(
            self.interaction,
            self.observed_property("mouse-pos"),
            self.help_controller.page,
            self.redraw_sub_picker,
            self.sidebar_view,
            self.hold_sidebar_scroll,
            self.scroll_tip,
            self.tip_scale.ref_h,
        )

    @property
    def click_target(self) -> surfaces.ClickTarget:
        """What a surface needs to decide whether it claims a left-click."""
        return surfaces.ClickTarget(
            self.interaction,
            sub_picker.DownloadPorts(
                self.toast,
                self.submit_subtitle_fetch,
                self._get,
                self.lifecycle_surfaces,
            ),
            self.sidebar_view,
            self.sidebar_actions,
            self.tip_ports,
            self.panel_ports,
            self.click_ports,
            self.hover_inputs,
        )

    @property
    def hover_actions(self) -> HoverActions:
        """The acts a hover decision performs, bound. Paired with `tip_ports`.

        Every callable resolves through `self` when it runs, which is what lets the applier and the
        routing cycle stop taking the host without freezing anything: an armed dwell that fires
        after an episode re-slot reaches the tip that exists then.
        """
        return HoverActions(
            arm=lambda kind, delay, intent: self.arm_hover_deadline(
                kind,
                delay,
                lambda: tooltip._dwell_elapsed(self.tip_ports, self.hover_actions, intent),
            ),
            cancel=self.cancel_hover_deadline,
            show_word=self.set_hover,
            retire_word=self.retire_hover,
            open_nested=lambda scan: nested_popup.show_nested(
                self.tip_ports, self.panel_ports, self.word_lookup, scan
            ),
            reveal_annotation=lambda revealed: self.set_annotation_hover(revealed=revealed),
            publish_engagement=lambda inside: setattr(self, "_mouse_in", inside),
        )

    @property
    def click_ports(self) -> ClickPorts:
        """What a click on a popup can do. Paired with `tip_ports` and `panel_ports`."""
        return ClickPorts(
            mine_token=self.mining_controller.mine_token,
            mine_current=lambda: self._stateless.run(mine_intents.MineCommand.WORD),
            speak_hovered=self.speak_hovered,
            click_preview=self._click_preview,
            cursor=lambda: self._get_mapping("mouse-pos") or None,
            paused=lambda: self.observed_property("pause"),
        )

    @property
    def hover_inputs(self) -> HoverInputs:
        """What the hover observation reads. Paired with `tip_ports` and `hover_actions`.

        `hover` and `cue_state` are callables because the sampled span reads them on both sides of
        the routing turn, and the turn is what changes the first of them.
        """
        return HoverInputs(
            mouse_pos=lambda: self.observed_property("mouse-pos"),
            hit=self._hit,
            hover=lambda: self.tooltip_controller.selected,
            cue_state=self._cue_state,
            tokens=self.tokens,
            boxes=self.boxes,
            sub_origin=self.sub_origin,
        )

    @property
    def word_lookup(self) -> WordLookup:
        """What a hover lookup reads, bound. Paired with `hover_inputs` and `show_actions`.

        Built per access so the generations are the ones current at the call: `prepare_hover_blocking`
        drops the worker lane for the duration of a deterministic hover, and a value cached across
        that would still report itself as deferred.
        """
        return WordLookup(
            tokenizer=self.profile_controller.tokenizer,
            dict_set=self.profile_controller.dict_set,
            mined=self.mining_controller.index_snapshot(),
            prefetch_gen=self.prefetch_state.gen,
            dependency_gen=self._dependency_generation,
            cue_identity=self._current_cue_identity,
            deferred=self.tooltip_controller.metadata_deferred,
            submit=self._request_interaction_metadata,
        )

    def _tooltip_apply(self) -> tooltip_controller.TooltipApply:
        """Fresh values for applying one tooltip worker completion on the owner thread."""
        return tooltip_controller.TooltipApply(
            ports=self.tip_ports,
            panel=self.panel_ports,
            lookup=self.word_lookup,
            hover=self.hover_inputs,
            show=self.show_actions,
            generation=self.prefetch_state.gen,
        )

    @property
    def show_actions(self) -> ShowActions:
        """What showing a hovered word does, bound. Paired with `tip_ports` and `panel_ports`."""
        return ShowActions(
            select=self.tooltip_controller.select,
            draw_cue=self.draw_subtitle,
            teardown=self.teardown_tip,
            bind_keys=self._bind_tip_keys,
            seed_precomposed=self._seed_precomposed,
            freeze=lambda *, already_paused: tooltip._freeze_frame(
                self.ipc,
                self.observed_property,
                enabled=self.tooltip_controller.pause_enabled,
                already_paused=already_paused,
            ),
            inflected=self._inflected_surface,
            sync_translation=self._sync_auto_translation,
            record_lookup=self._record_lookup,
        )

    def _record_lookup(self) -> None:
        if self.episode.session_recorder is not None:
            self.episode.session_recorder.record_lookup()

    def _cue_state(self) -> str:
        """How far this cue has got, as one fact rather than three reads into other features."""
        if not self.sub_text.strip():
            return "empty"
        if self._cue_retired:
            return "retired"
        return "pending" if self._sub_pending is not None else "ready"

    @property
    def preview_ports(self) -> miner_ui.PreviewPorts:
        """What the card-preview surface draws on and what a click on it does."""
        return miner_ui.PreviewPorts(
            interaction=self.interaction,
            surfaces=self.lifecycle_surfaces,
            osd=self.osd,
            tip_width=self.tip_scale.width,
            ipc=self.ipc,
            keys=self.keys,
            add_duplicate=lambda: self.mining_controller.force_duplicate(
                ForceDuplicate(miner_ui.duplicate_token(self.interaction.preview_panel))
            ),
            play_audio=self.play_audio,
        )

    @property
    def card_source(self) -> miner_ui.CardSource:
        """Where a preview's content comes from. Paired with `preview_ports`."""
        access = self.mining_controller.preview_access()
        return miner_ui.CardSource(
            deck=access.deck,
            model=access.model,
            fields=access.fields,
            note_info=access.note_info,
            fetch_image=access.fetch_image,
            fetch_media=access.fetch_media,
            lines=self.lines,
            provenance=self._provenance,
            video_path=lambda: self._get("path"),
            toast=self.toast,
        )

    @property
    def prefetch_ports(self) -> prefetch.PrefetchPorts:
        """What one speculative-warming pass reads. Paired with `head_probe`."""
        return prefetch.PrefetchPorts(
            enabled=bool(self.prefetch and self.profile_controller.dict_set is not None),
            engaged=bool(self.observed_property("pause")) or self._mouse_in,
            state=self.prefetch_state,
            cues=prefetch.LookaheadCues(
                self.episode.sub_index,
                self.sub_text,
                self.episode.nav_idx,
                self.prefetch_lookahead,
            ),
            tokens=self.tokens,
            styles=self.styles,
            tokenizer=self.profile_controller.tokenizer,
            inflected=self._inflected_surface,
            is_mined=self._is_mined,
            finish=self._finish_speculative_prefetch,
        )

    @property
    def head_probe(self) -> prefetch.HeadProbe:
        """What deciding a speculative HEAD render looks at. Paired with `prefetch_ports`."""
        return prefetch.HeadProbe(
            scorer=self.scorer,
            panel_key=self._panel_key,
            panel_cache=self.tip.panel_cache,
            lookahead=self.head_prefetch_lookahead,
        )

    @property
    def warm_ports(self) -> prefetch.WarmPorts:
        """What starting the background episode warm decides on."""
        return prefetch.WarmPorts(
            enabled=bool(self.prefetch and self.profile_controller.dict_set is not None),
            index=self.episode.sub_index,
            claim=self._claim_warm_index,
            annotate_async=self._annotation_async,
            start_annotation=self._start_episode_annotation,
            loop=prefetch.EpisodeWarmPorts(
                stop=self._stop,
                token_cache=self.token_cache,
                current_index=lambda: self.episode.sub_index,
                normalise=cue_key,
                tokenize=self._tokenize_cue,
            ),
        )

    def _claim_warm_index(self, index: CueIndex) -> bool:
        """Claim an index for the episode warm; `False` when it is already warmed or being warmed."""
        if self._warmed_index is index:
            return False
        self._warmed_index = index
        return True

    @property
    def listing_ports(self) -> sub_picker_module.ListingPorts:
        """What one subtitle listing needs to run and to publish itself back."""
        return sub_picker_module.ListingPorts(
            lister=self._sub_picker_lister,
            store=self._picker_store,
            redraw=self.redraw_sub_picker,
            submit=self._sub_picker_submit,
            stop=self._stop,
            current_episode=lambda: self.episode,
            toast=self.toast,
        )

    @property
    def capture_ports(self) -> backlog.CapturePorts:
        """What a bookmark toggle samples the cue from — read now, so the write is this cue."""
        return backlog.CapturePorts(
            video=self.text_property("path"),
            start=self._get_number("sub-start"),
            end=self._get_number("sub-end"),
            text=self.sub_text,
            secondary_text=self._secondary_text(),
            language=self.subtitle_language,
            tokens=self.tokens,
            hover=self.tooltip_controller.selected,
            jp_sid=self.jp_sid,
            en_sid=self.en_sid,
            tracks=self._get_sequence("track-list"),
            store=lambda: sidebar_module._ensure_store(self.session),
            toast=self.toast,
            record_capture=self._record_capture,
        )

    def _record_capture(self) -> None:
        if self.episode.session_recorder is not None:
            self.episode.session_recorder.record_capture()

    @property
    def reslot_ports(self) -> episode_reslot.ReslotPorts:
        """What re-slotting the overlay onto a newly loaded episode does."""
        return episode_reslot.ReslotPorts(
            ipc=self.ipc,
            finish_stats=self.finish_session_stats,
            start_stats=self._open_session_history,
            rebind_episode=self.rebind_episode,
            rebuild_index=self.rebuild_sub_index,
            configure_mode=self.configure_subtitle_mode,
            configure_retry=self.configure_subtitle_retry,
            configure_picker=self.configure_sub_picker,
            fetch_japanese=self.fetch_japanese_subs_async,
            start_prefetch=self.start_prefetch,
            toast=self.toast,
        )

    @property
    def watch_ports(self) -> episode_reslot.WatchPorts:
        """What wiring the follow-mpv-onto-the-next-episode hooks needs."""
        return episode_reslot.WatchPorts(
            install_reslot_hook=self.install_reslot_hook,
            set_advance_hook=lambda hook: setattr(self, "advance_hook", hook),
            prop=self.observed_property,
            current_media_path=self.current_media_path,
        )

    @property
    def tip_ports(self) -> TipPorts:
        """What the popup blit/scroll/placement chain needs, as one member rather than the set.

        A property for the same reason `panel_style` is one: as a host-taking builder it would
        trade the chain's debt rows for one more, and every caller in the chain would still inherit
        everything it gathers.
        """
        return TipPorts(
            tip=self.tip,
            pulse_store=self.tooltip_controller.pulse_store,
            pause_store=self.tooltip_controller.pause_store,
            word_store=self.tooltip_controller.word_store,
            scale=self.tip_scale,
            surfaces=self.interaction_surfaces,
            hover_store=self.tooltip_controller.hover_store,
            nav_store=self.tooltip_controller.nav_store,
            request_render_ahead=self._request_render_ahead,
            osd=self.osd,
            nested_max_frac=self.nested_max_frac,
            peek_render_cache=self._peek_render_cache,
            schedule_flash_expiry=self.schedule_flash_expiry,
            toast=self.toast,
            request_engaged_tooltip=self._request_engaged_tooltip,
        )

    @property
    def engaged_build_ports(self) -> tooltip_engaged.EngagedBuildPorts:
        """The exact session capabilities available to tooltip build workers."""
        return tooltip_engaged.EngagedBuildPorts(
            nested_max_frac=self.nested_max_frac,
            tip_scale=lambda: self.tip_scale,
            panel_for=self._panel_for,
            worker_seed_head=self._worker_seed_head,
            precompose_head=self._precompose_head,
            mem_fill=self._mem_fill,
            cap_for=self._cap_for,
            navigated_panel=self._navigated_panel,
            engaged_open_panel=self._engaged_open_panel,
        )

    @property
    def panel_style(self) -> tooltip_panel.PanelStyle:
        """The session-lifetime half of a panel build, as one value.

        A property and not a snapshot function: as the latter every caller in the build chain
        inherited all eleven reads it gathers, which is most of what made the tooltip cluster
        measure as coupled to the host. Per-turn facts — the mined set, the scroll flag — stay
        parameters, because a value holding those would be the SessionController under another name.
        """
        return tooltip_panel.PanelStyle(
            width=self.tip_scale.width,
            band_cache_max=self.band_cache_max,
            raw_band_ceiling=self.raw_band_ceiling,
            layout_backend=self.layout_backend,
            layout_engine=self.layout_engine,
            add_button=self.mining_controller.target_available,
            speak_button=self._tts_ok,
            dict_set=self.profile_controller.dict_set,
            scorer=self.scorer,
            tokenizer=self.profile_controller.tokenizer,
            kanji_stroke_order=self.kanji_stroke_order,
        )

    def _panel_for(
        self,
        tok,
        inflected=None,
        min_h: int | None = None,
        *,
        mined: bool | None = None,
        nested: bool = False,
        extra_terms: tuple[str, ...] = (),
        group_mined: tuple[bool, ...] | None = None,
    ):
        return tooltip_panel.panel_for(
            self.panel_ports,
            tok,
            inflected,
            min_h,
            mined=mined,
            nested=nested,
            extra_terms=extra_terms,
            group_mined=group_mined,
        )

    def _panel_cache_setdefault(self, key, st) -> Panel:
        return self.tooltip_controller.cache_setdefault(key, st)

    # --- persistent render cache (#149): seed a cold hover's first viewport from disk ----------
    def _render_cache(self) -> RenderCache | None:
        """The cross-session render cache, USED WHEN AVAILABLE: opened lazily only if a prebuilt
        ``render-cache.sqlite`` already exists (``saitenka prewarm`` builds it). ``None`` when opted out,
        no dict set, or no prebuilt cache — so a fresh install creates nothing and costs nothing."""
        rc = self.session.render_cache
        if not rc.cache_on or self.profile_controller.dict_set is None:
            return None
        if not rc.built:
            rc.built = True
            from saitenka.app.render_cache import RenderCache

            path = cache_dir() / "render-cache.sqlite"
            if path.exists():  # use-when-available — prewarm is the builder, not a live session
                rc.obj = RenderCache.open(path, max_bytes=rc.cache_max_bytes)
        return rc.obj

    def _finish_mask_atlas_startup(self, completion: EffectFinished) -> None:
        opened = mask_atlas_startup.finish(self._mask_atlas_startup, completion)
        if opened is not None:
            mask_atlas_startup.install(self.session.render_cache, opened)

    def _render_cache_sig(self) -> str:
        """The current ``config_sig`` (format+width+cap+dict-set), memoised per (width, cap) so a
        resolution change recomputes it. Only called when the cache is on (dict_set present)."""
        rc = self.session.render_cache
        cap = self.tip_scale.cap
        ck = (self.tip_scale.width, cap)
        if rc.config_sig is None or rc.sig_key != ck:
            from saitenka.app.render_cache import config_signature, dict_set_signature

            assert (
                self.profile_controller.dict_set is not None
            )  # _render_cache() gated on it before any caller reaches here
            rc.config_sig = config_signature(
                width=self.tip_scale.width,
                cap=cap,
                dict_sig=dict_set_signature(self.profile_controller.dict_set),
            )
            rc.sig_key = ck
        return rc.config_sig

    def _render_cache_min_height(self) -> int:
        """Cost gate (px): only heads at least this tall — a non-trivial entry that needs scrolling, the
        pathological tail whose cold build+raster blows the budget — are persisted."""
        return self.session.render_cache.cache_min_height_px

    def _mem_cache(self):
        """The in-memory tier-2 compressed-head cache (RAM), or ``None`` when the render cache is off.
        The MAIN thread reads ONLY this — never SQLite — so a cold hover inflates from RAM; the prefetch
        worker hydrates it from disk (see :meth:`_worker_seed_head` / :meth:`_mem_fill`)."""
        rc = self.session.render_cache
        return rc.mem if rc.cache_on else None

    def _peek_render_cache(self, key):
        """The stored first viewport + ``full_h`` for ``key`` (direct-paint path), or ``None`` — read
        from the in-memory tier-2 (inflate from RAM), NEVER from SQLite: the main thread must not open the
        DB on the hover path. A miss falls through to a build (and the tier-3 deferred render)."""
        mem = self._mem_cache()
        if mem is None or len(mem) == 0:
            return None  # empty tier-2 → miss without computing the sig (nothing a worker has seeded yet)
        from saitenka.app.render_cache import content_key

        cv = mem.get((self._render_cache_sig(), content_key(key)))
        if cv is None:
            return None
        try:
            return cv.inflate()
        except ValueError:  # a garbled blob won't reshape → safe miss, build instead
            return None

    def _seed_precomposed(self, st: Panel, key, cap: int) -> bool:
        """Seed ``st``'s first viewport from the in-memory tier-2 (cold-show fast path). ``False`` when
        the cache is off or misses, or the stored geometry doesn't match this show's ``view_h``. Main
        thread — an in-RAM inflate on a hit, no disk."""
        loaded = self._peek_render_cache(key)
        if loaded is None:
            return False
        view_h = min(st.full_height, cap)
        if view_h <= 0 or loaded.view_h != view_h or loaded.overscan != view_h:
            return False  # geometry moved (content/height changed) — safe miss, live-render
        st.windowed.install_first_view(loaded.view_h, loaded.overscan, loaded.array)
        return True

    def _worker_seed_head(self, st: Panel, tok, inflected, *, mined: bool, cap: int) -> bool:
        """WORKER-side: seed ``st``'s first viewport from the on-disk cache (inflate off the main thread)
        and mirror the compressed blob into the in-memory tier-2, so the next hover paints from RAM. Far
        cheaper than a raster on a hit — the lookahead worth doing before falling back to a full compose.
        ``False`` on cache-off / miss / geometry mismatch (caller then rasters)."""
        cache = self._render_cache()
        mem = self._mem_cache()
        if cache is None or mem is None:
            return False
        from saitenka.app.render_cache import content_key

        sig = self._render_cache_sig()
        ck = content_key(self._panel_key(tok, inflected, mined=mined))
        cv = cache.peek_compressed(sig, ck)
        if cv is None:
            return False
        view_h = min(st.full_height, cap)
        if view_h <= 0 or cv.view_h != view_h or cv.overscan != view_h:
            return False
        st.windowed.install_first_view(cv.view_h, cv.overscan, cv.inflate().array)
        mem.put((sig, ck), cv)
        return True

    def _mem_fill(self, tok, inflected, *, mined: bool) -> None:
        """WORKER-side: mirror a just-composed+persisted head into the in-memory tier-2 (compressed), so a
        later hover — or an evicted-then-rebuilt panel — re-seeds from RAM without touching SQLite. No-op
        if the head wasn't stored (cost gate) or the cache is off; reuses the on-disk blob (no re-compress)."""
        cache = self._render_cache()
        mem = self._mem_cache()
        if cache is None or mem is None:
            return
        from saitenka.app.render_cache import content_key

        sig = self._render_cache_sig()
        ck = content_key(self._panel_key(tok, inflected, mined=mined))
        cv = cache.peek_compressed(sig, ck)
        if cv is not None:
            mem.put((sig, ck), cv)

    def _precompose_head(
        self, st: Panel, tok, inflected, *, mined: bool, cap: int, protected: bool = False
    ) -> None:
        """Precompose ``st``'s first viewport in idle (the prefetch lane) and, when the persistent
        cache is on, write a cost-gated head to disk for a later session's cold hover. ``protected`` (the
        offline prewarm) marks the popular set eviction-last so live write-back can't thrash it."""
        cache = self._render_cache()
        if cache is None:
            st.precompose_head(cap)
            return
        from saitenka.app.render_cache import content_key

        key = self._panel_key(tok, inflected, mined=mined)
        st.precompose_head(
            cap,
            cache=cache,
            config_sig=self._render_cache_sig(),
            content_key=content_key(key),
            min_height=self._render_cache_min_height(),
            protected=protected,
        )

    # --- background prefetch (warm the current/next line's tooltips) — logic in app/prefetch.py --
    def start_prefetch(self) -> None:
        prefetch.start_prefetch(
            self.ipc,
            self.prefetch_state,
            prefetch.HostPrefetchBackend(self),
            self.profile_controller.tokenizer,
            self.prefetch_workers,
            enabled=bool(self.prefetch and self.profile_controller.dict_set is not None),
        )

    def _update_prefetch(self) -> None:
        generation = self.prefetch_state.gen
        prefetch.update_prefetch(self.prefetch_ports, self.head_probe)
        if self.prefetch_state.gen != generation:
            self.tooltip_controller.cancel_current_work()

    def _finish_speculative_prefetch(self, completion: EffectFinished) -> None:
        prefetch.finish(self.prefetch_state, completion, self._finish_speculative_prefetch)

    def _inflected_surface(self, index: int) -> str:
        return self.profile_controller.tokenizer.inflected_in(self.tokens, index)

    def _telemetry_gauges(self) -> dict[str, float]:
        """Live cache-size gauges for the telemetry interval sampler (writer thread, ~1s cadence — NOT
        the hot path). ``panel_cache.bytes`` is the retained (compressed) on-heap footprint;
        ``dict_cache.size`` the decoded-entry count across every dictionary. The tooltip owner reads
        its cache under the lock it owns, so a concurrent prefetch mutation cannot fault iteration."""
        panel_n, panel_bytes = self.tooltip_controller.cache_totals()
        dict_n = (
            self.profile_controller.dict_set.decoded_entry_count()
            if self.profile_controller.dict_set is not None
            else 0
        )
        gauges = {
            "panel_cache.size": float(panel_n),
            "panel_cache.bytes": float(panel_bytes),
            "dict_cache.size": float(dict_n),
        }
        if self.native_geometry is not None:
            stats = self.native_geometry.worker.stats
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

    def _cap_for(self, frac: float) -> int:
        return prefetch.cap_for(frac)

    def _show_tooltip(self, index: int) -> None:
        tooltip.show_tooltip(
            self.tip_ports, self.panel_ports, self.hover_inputs, self.show_actions, index
        )

    def _bind_tip_keys(self) -> None:
        """Register the tooltip-scoped keys (idempotent — word switches must not re-bind)."""
        if not self.tooltip_controller.claim_keybindings():
            return
        if self.help.open:
            return
        for binding in active_bindings(self.keys, "tooltip"):
            self.ipc.command_async("keybind", binding.key, f"script-message {binding.spec.message}")

    def _unbind_tip_keys(self) -> None:
        """Neutralise the tooltip keys so a leaked bind can't fire ``tab-prev``/etc. when no tooltip is
        up. mpv has no unbind verb over IPC, and ``keybind KEY ""`` is REJECTED — it logs the noisy
        ``[input] Command name missing`` / ``Invalid command for key binding 'LEFT': ''`` triple (visible
        on the Windows console; silently on the mac log). Rebind to the valid no-op ``ignore`` instead:
        no error, and the key stops doing tooltip work while the popup is gone."""
        if not self.tooltip_controller.release_keybindings():
            return
        if self.help.open:
            return
        for binding in active_bindings(self.keys, "tooltip"):
            self.ipc.command_async("keybind", binding.key, "ignore")

    def _define_mouse_section(self) -> None:
        self._mouse.define(active_bindings(self.keys, "mouse"))

    def _wants_mouse_capture(self) -> bool:
        return self.surface_router.wants_mouse_capture()

    def _sync_mouse_capture(self) -> None:
        self._mouse.sync()

    def _assert_mouse_capture(self) -> None:
        self._mouse.take()

    def _release_mouse_capture(self) -> None:
        self._mouse.release()

    @property
    def _mouse_captured(self) -> bool:
        return self._mouse.held

    @property
    def _mouse_section_defined(self) -> bool:
        return self._mouse.defined

    def _render_tip_view(self) -> None:
        tooltip_panel.render_view(self.tip_ports, self.tip.view)

    def _render_nested_view(self) -> None:
        tooltip_panel.render_view(self.tip_ports, self.tip.nest)

    def scroll_tip(self, delta: int) -> None:
        # event → redraw-finished latency for one scroll tick: nests the downstream "render"
        # (banded block-cache miss) and "upload" (OSD blit) spans, so a scroll-frame trace_id
        # groups the whole chain instead of leaving "upload" as an orphaned, unrelated span.
        self._scrolled_this_tick = True
        with otel_metrics.instrumented_jank(
            otel_metrics.scroll_frame_duration_ms,
            otel_metrics.scroll_frame_jank,
            otel_metrics.SCROLL_JANK_THRESHOLD_MS,
            "scroll_frame",
            layout_backend=self.layout_engine,
        ) as span:
            tooltip.scroll_tip(self.tip_ports, self.hover_actions, delta)
            st = self.tip.view.state
            if st is not None:
                # Attribute a janky frame: bands rastered synchronously (render_ahead was behind) and
                # the panel's height. A warm frame is bands=0; the jank tail is the frames with bands>0.
                span.set("bands", st.last_frame_rasters)
                span.set("full_h", st.full_height)
                # Where the wheel asked to be vs where the pixels are. The two diverging across a
                # whole burst is the shape of a scroll that arrives and never lands — otherwise
                # only inferable by cross-reading `scroll_request` outcomes.
                view = self.tip.view
                span.set("scroll", view.scroll)
                span.set("desired", view.desired_scroll)
                # ...and which predicate refused, since publication asks the 1x tier while a hi-dpi
                # blit composites from the native one. Both cold means the destination is starved;
                # native warm with soft cold means the gate is pointed at the wrong cache.
                vh = min(view.view_h, st.full_height)
                span.set("warm", st.viewport_warm(view.desired_scroll, vh))
                span.set(
                    "native_warm",
                    st.native_viewport_warm(view.desired_scroll, vh, self.tip_scale.raster),
                )
                # Crisp health per scroll frame: the display scale (does it jitter mid-scroll?) and the
                # soft-fallback reason ("" = composited crisp) — so a soft run is attributable to a cause.
                span.set("scale", f"{self.tip_scale.display:.4f}")
                span.set("crisp_miss", self.tip.view.crisp_miss or "n/a")

    def _navigated_panel(self, query: str):
        """The read-only reference Panel for a nav target — built off the main thread by the engaged
        tooltip lane, so the seam lives on the SessionController (no engaged-tooltip→tooltip import)."""
        return tooltip._navigated_panel(self.panel_style, query)

    # --- what InteractionHost reads and calls ------------------------------------------
    @property
    def tip_can_go_back(self) -> bool:
        """A link-navigation step is available to pop — the fact, split from the act."""
        return self.interaction.tip_nav.can_go_back

    @cached_property
    def _stateless(self) -> StatelessRouter:
        """The stateless half's route table, built on first use.

        Lazy rather than a line in `__init__`: the adapters read the host through the reference they
        hold, so nothing here depends on how far construction has got, and the composition root does
        not grow a row per feature.
        """
        return StatelessRouter(stateless_features(self))

    def _engaged_open_panel(self, source: str, query: str, *, mined: bool | None = None):
        """The (cached) panel for a clicked/keyed nested open — the shared builder the engaged-tooltip
        lane and session thread reach via the SessionController seam. The worker
        passes ``mined`` (jamdict isn't worker-safe); the main thread lets it recompute."""
        return nested_popup._engaged_open_panel(
            self.tip_ports, self.panel_ports, source, query, mined=mined
        )

    # --- kanji lookup mode ------------------------------------------------------------------------
    def kanji_current(self) -> None:
        self._stateless.run(hover_intents.HoverCommand.KANJI)

    def open_kanji(self, ch: str, wx: float, wy: float, wh: float) -> None:
        nested_popup.open_kanji(self.tip_ports, self.panel_ports, ch, wx, wy, wh)

    def _hide_nested(self) -> None:
        nested_popup.hide_nested(self.tip_ports)

    # --- mining -------------------------------------------------------------------------------
    def _assemble_mining_controller(
        self,
        identity: MiningIdentity,
        anki: Anki | None,
        config: MineConfig | None,
    ) -> MiningController:
        return MiningController.for_session(
            identity,
            anki,
            config,
            MiningSessionAssembly(
                ipc=self.ipc,
                capability_submit=self._capability_submit,
                timers=self.lifecycle_timers,
                stopped=self._stop.is_set,
                settings=self.options.mining,
                encounter=self._mining_encounter,
                apply=self._mining_apply,
            ),
        )

    def _mining_encounter(self) -> miner.MiningEncounter:
        return miner.MiningEncounter(
            cue=MineCue(
                self.tokens,
                self.styles,
                self.tooltip_controller.selected,
                self.profile_controller.tokenizer,
                self.options.mining.max_bulk,
            ),
            dict_set=self.profile_controller.dict_set,
            ipc=self.ipc,
            media_path=self.text_property("path"),
            playhead=self._get_number("time-pos") or 0.0,
            sentence_html=self._sentence_html(),
            hovered_terms=self.interaction.hovered_word_meta.terms,
        )

    def _mining_apply(self) -> miner.MiningApply:
        preview = self.interaction.preview_panel

        def reset_capture() -> None:
            preview.last_jpg = preview.last_audio = None

        def record_mined(count: int) -> None:
            if self.episode.session_recorder is not None:
                self.episode.session_recorder.record_mined(count)

        return miner.MiningApply(
            toast=self.toast,
            reset_capture=reset_capture,
            captured_image=lambda path: setattr(preview, "last_jpg", path),
            captured_audio=lambda path: setattr(preview, "last_audio", path),
            mark_mined=self._mark_mined,
            mined_here=self._sidebar_mined_here,
            remember_duplicate=lambda token: setattr(preview, "dup_tok", token),
            preview_existing=self._preview_existing,
            preview_mined=self._preview_mined,
            record_mined=record_mined,
        )

    def _sidebar_mined_here(self) -> None:
        """Mark the backlog rows covering this cue mined and redraw. The sidebar's own read set stays
        the sidebar's — mining asks for the act, not for the view."""
        from saitenka.app import sidebar

        sidebar.mine_active(self.sidebar_view)

    def _sentence_html(self) -> str:
        return "<br>".join("".join(t.surface for t in line) for line in self.lines)

    def _provenance(self, video) -> str:
        return miner.provenance(self._get_number("time-pos") or 0.0, video)

    # --- cue capture -------------------------------------------------------------------
    def has_active_cue(self) -> bool:
        """A cue with a path and timings is on screen — what a bookmark would capture."""
        return bool(
            self._get("path")
            and self._get("sub-start") is not None
            and self._get("sub-end") is not None
            and self.sub_text.strip()
        )

    def _mark_mined(self, expression: str) -> None:
        mined_feedback.mark_mined(
            self.tip_ports,
            self.panel_ports,
            self.hover_inputs,
            self.show_actions,
            expression,
        )

    # --- card preview (verify correctness / image / sound, one surface) — logic in app/miner_ui.py
    def sentence_lines(self) -> list[str]:
        return miner_ui.sentence_lines(self.lines)

    def _preview_mined(self, card, tok, video, status: str = "mined") -> None:
        if not self.show_preview:
            self.toast(f"mined {card.expression}")  # preview off → a terse confirmation instead
            return
        miner_ui.preview_mined(self.preview_ports, self.card_source, card, tok, video, status)

    def _preview_existing(self, note_id: int, card, status: str) -> None:
        if not self.show_preview:
            self.toast(f"already have {card.expression}")
            return
        miner_ui.preview_existing(self.preview_ports, self.card_source, note_id, card, status)

    def _render_preview(self) -> None:
        miner_ui.render_preview(
            self.interaction, self.lifecycle_surfaces, self.osd, self.tip_scale.width
        )

    def _hide_preview(self) -> None:
        self._stateless.run(panel_intents.PanelCommand.CLOSE_CARD_PREVIEW)

    def _click_preview(self, x: float, y: float) -> bool:
        return miner_ui.click_preview(self.preview_ports, x, y)

    def replay_preview(self) -> None:
        self._stateless.run(panel_intents.PanelCommand.REPLAY_CARD_PREVIEW)

    # --- translation reveal (EN secondary track) ----------------------------------------------
    def setup_secondary(self) -> int | None:
        return subtitle_modes.setup_secondary(self.track_ports)

    @property
    def translate_on(self) -> bool:
        """The manual translation hold, off `Owner.PRESENTATION`'s slice.

        The setter is a declaration, not a back door: assigning it is the same event the toggle
        sends, so a caller establishing this precondition takes the path production takes.
        """
        return self.translation_store.current.held

    @translate_on.setter
    def translate_on(self, held: bool) -> None:
        self.translation_store.dispatch(events.TranslationHeld(held))

    @property
    def _trans_text(self) -> str | None:
        """What the translation surface is showing. Read-only: it is set by drawing it."""
        return self.translation_store.current.drawn

    def translation_visible(self) -> bool:
        # Two conditions, and not interchangeable: whether the user wants it, and whether
        # saitenka is drawing anything at all. Code deciding what to do once the surfaces come
        # back needs the first without the second — see `translation_wanted`.
        return self.ov.visible and self.translation_wanted()

    def translation_wanted(self) -> bool:
        """Whether the user wants the secondary line, independent of whether anything is drawn.

        `toggle_overlay` decides what to do *after* the surfaces return, so it must not ask
        `translation_visible` — that answers False precisely because the overlay is still hidden.
        """
        return self.translate_on or (self.auto_translate and self.tooltip_controller.selected >= 0)

    def _sync_auto_translation(self) -> None:
        if not self.auto_translate:
            return
        self.reveal_translation() if self.translation_visible() else self.hide_translation(
            release=not self.translate_on
        )

    def toggle_translation(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.TOGGLE_TRANSLATION)

    # --- what SessionHost reads and calls ----------------------------------------------
    def toggle_overlay(self) -> None:
        self._stateless.run(session_intents.SessionCommand.TOGGLE_OVERLAY)

    def cycle_profile(self) -> None:
        self._stateless.run(profile_intents.ProfileCommand.CYCLE)

    def configure_subtitle_mode(
        self, startup: subtitle_modes.SubtitleStartup, *, slang: str = "ja,jpn,jp"
    ) -> None:
        subtitle_modes.configure(
            startup,
            slang=slang,
            declare=self.declare_subtitle,
            activate=lambda sid: self.subtitle_pipeline.activate(
                self.subtitle_target(), sid, draw=self.draw_subtitle
            ),
            secondary_sid=self._get("secondary-sid"),
            ipc=self.ipc,
            invalidate=self.invalidate_analysis,
        )

    # --- what HoverHost reads and calls ------------------------------------------------

    def resume_after_hover_pause(self) -> None:
        """Give playback back, if a tooltip is what took it. One path, because there were three: two
        reached mpv through a `getattr(ipc, "command_async", ipc.command)` duck-probe — invisible to
        the direct-write gate, and a fake missing the port silently took the other branch.

        Whether anything is owed is the slice's answer, not a flag read here: both callers used to
        ask and then act, and the clearing happened at a third site."""
        if not self.tooltip_controller.release_pause_claim():
            return
        send_correlated(
            self.ipc,
            "hover-pause-resume",
            "set_property",
            "pause",
            False,  # noqa: FBT003  # mpv IPC wire value
            owner=Owner.PLAYBACK,
        )

    @property
    def picker_store(self) -> PickerStore:
        """The picker's store, for a feature adapter that must not reach into `_get`-style names.

        An adapter declares its host surface as a protocol, so every member it names has to be part
        of the public one — a private in a port is a coupling nothing outside can honour.
        """
        return self._picker_store

    def property_value(self, name: str) -> object | None:
        """One mpv property as last observed. `_get` under a name a port can declare."""
        return self._get(name)

    @property
    def sidebar(self) -> SidebarState:
        """Where the sidebar is. Read-only for the same reason `help` and `sub_picker` are."""
        return self.interaction.sidebar

    @property
    def sub_picker(self) -> PickerState:
        """What the picker is showing. Read-only for the same reason `help` is: the slice owns it."""
        return self.interaction.sub_picker

    @property
    def preview(self) -> CardPreview:
        """What the card preview is showing. Read-only, like the other three."""
        return self.interaction.preview

    def redraw_sub_picker(self) -> None:
        """Lay the picker out for this screen and present it, storing the geometry hit-testing uses."""
        from saitenka.app import sub_picker

        state = self.sub_picker
        if not state.open:
            return
        rendered, x, y, width, height = sub_picker.picker_panel(
            state, osd=self.osd, scale=self.chrome_scale, close_key=self.keys.sub_picker_key
        )
        panel = self.interaction.picker_panel
        panel.rect = (x, y, width, height)
        panel.hits = rendered.hitboxes
        self.lifecycle_surfaces.present(rendered.image, x, y, oid=OverlayId.PICKER)

    # --- episode analysis: state module, executed here ------------------------------------------
    def _draw_analysis(self) -> None:
        if not self.analysis.open:
            return
        image = analysis_overlay.panel_image(
            self.analysis,
            osd=self.osd,
            close_key=self.keys.analysis_key,
            scale=self.chrome_scale,
        )
        x = (self.osd[0] - image.width) // 2
        y = (self.osd[1] - image.height) // 2
        self.lifecycle_surfaces.present(image, x, y, oid=OverlayId.ANALYSIS)

    def _refresh_analysis(self) -> None:
        """Bring the analysis up to date and show it. Presenting is the host's, deciding is not."""
        analysis_overlay.request(
            self.analysis,
            language=self.subtitle_language,
            index=self.episode.sub_index,
            loading=self._loading,
            scorer=self.scorer,
            tokenizer=self.profile_controller.tokenizer,
        )
        self._draw_analysis()
        if analysis_overlay.submit_pending(
            self.analysis, self._analysis_submit, self._finish_analysis
        ):
            self._draw_analysis()

    def set_analysis_open(self, *, open: bool) -> None:  # noqa: A002
        self.analysis.open = open
        if not open:
            self.lifecycle_surfaces.remove(OverlayId.ANALYSIS)
            return
        self._refresh_analysis()

    def invalidate_analysis(self, *, vocabulary_changed: bool = False) -> None:
        analysis_overlay.invalidate(self.analysis, vocabulary_changed=vocabulary_changed)
        self._refresh_analysis()

    # --- help overlay: the slice decides, this performs ----------------------------------------
    @property
    def help(self) -> HelpState:
        """Read-only public projection of the Help owner's state."""
        return self.help_controller.state

    def toggle_subtitle_language(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.TOGGLE_LANGUAGE)

    def mark_current_subtitle_japanese(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.MARK_CURRENT_JAPANESE)

    def fetch_japanese_subs_async(self, fetch) -> None:
        subtitle_modes.start_fetch(
            self.submit_subtitle_fetch, self._get, fetch, select_if_unchanged=True
        )

    def configure_subtitle_retry(self, factory) -> None:
        subtitle_modes.configure_retry(self.episode.subtitle, factory)

    def retry_japanese_subtitles(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.RETRY_ACQUISITION)

    def _secondary_text(self) -> str:
        return translation.clean_secondary(self.observed_property("secondary-sub-text"))

    def draw_translation(self) -> None:
        text = self._secondary_text()
        self.translation_store.dispatch(events.TranslationDrawn(text))
        if not text:
            self.lifecycle_surfaces.remove(OverlayId.TRANS)
            return
        image, x, y = translation.render_translation(text, self.osd)
        self.lifecycle_surfaces.present(image, x, y, oid=OverlayId.TRANS)

    def reveal_translation(self) -> None:
        self.setup_secondary()
        self.draw_translation()

    def hide_translation(self, *, release: bool) -> None:
        """Take the overlay down. `release` hands the secondary track back to mpv, which only the
        paths that own the reveal may do — an auto-reveal ending must not release a track the
        manual toggle is still holding."""
        self.lifecycle_surfaces.remove(OverlayId.TRANS)
        self.translation_store.dispatch(events.TranslationDrawn(None))
        if release:
            subtitle_modes.release_secondary(self.track_ports)

    def toast(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        img = render_toast(text, kind)
        x = (self.osd[0] - img.width) // 2
        y = round(self.osd[1] * 0.08)
        self.lifecycle_surfaces.present(img, x, y, oid=TOAST_ID)

        scheduled = self.lifecycle_timers.schedule(
            LifecycleTimerKind.TOAST_EXPIRY,
            seconds,
            lambda: self.lifecycle_surfaces.remove(TOAST_ID),
        )
        if not scheduled:
            self.lifecycle_surfaces.remove(TOAST_ID)

    def toggle_hover_pause(self) -> None:
        self._stateless.run(hover_intents.HoverCommand.TOGGLE_PAUSE)

    def toggle_bookmark(self) -> None:
        self._stateless.run(mine_intents.MineCommand.BOOKMARK_CUE)

    def toggle_sidebar(self) -> None:
        self._stateless.run(panel_intents.PanelCommand.TOGGLE_SIDEBAR)

    def toggle_sub_picker(self) -> None:
        self._stateless.run(panel_intents.PanelCommand.TOGGLE_SUBTITLE_PICKER)

    def configure_sub_picker(self, lister: Callable[[str], tuple]) -> None:
        """Enable the picker for this session with a provider-agnostic candidate lister. Called
        wherever the subtitle-retry factory is wired, so the key binding is a no-op (with a toast)
        unless at least one provider is enabled."""
        self._sub_picker_lister = lister

    def toggle_analysis(self) -> None:
        self._stateless.run(panel_intents.PanelCommand.TOGGLE_ANALYSIS)

    def toggle_annotation_mode(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.TOGGLE_ANNOTATION_MODE)

    def _register_keybinds(self) -> None:
        """Install the "global"-scoped bindings as ONE mpv input section.

        A `keybind` per key would put ~24 correlated commands in flight before the reactor drains,
        each reserving two terminal slots against a 64-slot mailbox — over the bound a bind is
        dropped with only a log line, i.e. a dead shortcut, and the inline-completing fake cannot
        reproduce it. One section is one command with one outcome.

        FORCED, so a user's input.conf does not shadow these — see `bindings.GLOBAL_SECTION` for why
        `keybind`'s own priority was the F1 regression. The "mouse" scope stays a separate forced
        section because it is enabled on demand rather than for the whole session.
        """
        # active_bindings no longer gates on `requires` — bind the anki/tts actions even when the dep
        # isn't up YET (attach mode loads Anki async, after this runs, and we never re-register). The
        # handlers (mine_current/bulk_mine/speak) no-op with a toast when the dep is absent.
        bindings = [b for b in active_bindings(self.keys, "global") if b.spec.message is not None]
        contents = section_contents(bindings)
        if contents:
            send_correlated(
                self.ipc,
                "define-global-section",
                "define-section",
                GLOBAL_SECTION,
                contents,
                "force",
                owner=Owner.INTERACTION,
            )
            send_correlated(
                self.ipc,
                "enable-global-section",
                "enable-section",
                GLOBAL_SECTION,
                owner=Owner.INTERACTION,
            )
        log.info(
            "registered %d global keybinds (anki=%s)",
            len(bindings),
            self.mining_controller.configured,
        )
        self._define_mouse_section()  # "mouse"-scoped controls live in a forced section, enabled on demand

    def _navigate_previous(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.NAVIGATE_PREVIOUS)

    def _navigate_next(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.NAVIGATE_NEXT)

    def _replay_cue(self) -> None:
        self._stateless.run(subtitle_intents.SubtitleCommand.REPLAY_CUE)

    def _anchor_subtitles(self) -> None:
        """One-press manual re-time: snap the sub cue nearest the playhead to start NOW. For when
        auto-sync leaves a residual offset (e.g. a different-length OP), pause as a line's audio
        begins and press — mpv's ``sub-delay`` shifts so that cue lands here, and every later cue
        follows by the same offset. The overlay reads the delayed ``sub-text``, so the on-screen line
        moves with it. Cumulative (anchors from the current delay), so a second anchor refines a first."""
        self._stateless.run(subtitle_intents.SubtitleCommand.ANCHOR_TIMING)

    def _build_command_router(self) -> CommandExecutor:
        """Assemble feature-owned actions once; handlers are bound and receive no god context."""

        def action(method: Callable[..., object]) -> Callable[[], None]:
            """Route to a `SessionController` verb, resolved by name at call time.

            Annotated by what it reads — `__name__` — and not as a `SessionController` method, because a
            parameter that names the host is a host parameter to the contract gate whether or not
            an instance ever reaches it. `SessionController.verb` is what every caller passes; a typo in one
            raises at router construction, on the class.

            Late binding is deliberate: a keybind test overrides the verb on the *instance* and
            then presses the key, so a row that captured the bound method would route past the
            override. Taking the function rather than its name is what makes the row a real
            reference — a rename carries the table with it instead of failing at the next
            keypress, and a verb reached only from here stops looking dead to every tool that
            resolves symbols.
            """
            name = method.__name__
            return lambda: getattr(self, name)()

        def interaction(command) -> Callable[[], None]:
            return lambda: self._stateless.run(command)

        # Every row's decision is a pure reducer — `subtitle_intents`, `runtime.help`,
        # `hover_intents`, `mine_intents`, `panel_intents`, `session_intents` or
        # `interaction_intents`. The verb below only carries the intent to one of them.
        handlers: dict[str, Callable[[], None]] = {
            **self._assembly.command_handlers(),
            SUBTITLE_LANGUAGE_MSG: action(SessionController.toggle_subtitle_language),
            SUBTITLE_MARK_JP_MSG: action(SessionController.mark_current_subtitle_japanese),
            SUBTITLE_RETRY_MSG: action(SessionController.retry_japanese_subtitles),
            LEGACY_RENDERER_MSG: action(SessionController.toggle_legacy_renderer),
            ANNOTATION_MSG: action(SessionController.toggle_annotation_mode),
            TRANS_MSG: action(SessionController.toggle_translation),
            COPY_LINE_MSG: action(SessionController.copy_line),
            SUB_PREV_MSG: action(SessionController._navigate_previous),
            SUB_NEXT_MSG: action(SessionController._navigate_next),
            SUB_REPLAY_MSG: action(SessionController._replay_cue),
            SUB_ANCHOR_MSG: action(SessionController._anchor_subtitles),
            SPEAK_MSG: action(SessionController.speak_hovered),
            COPY_MSG: action(SessionController.copy_hovered),
            KANJI_MSG: action(SessionController.kanji_current),
            HOVER_PAUSE_MSG: action(SessionController.toggle_hover_pause),
            MINE_MSG: lambda: self._stateless.run(mine_intents.MineCommand.WORD),
            MINE_VIDEO_MSG: lambda: self._stateless.run(mine_intents.MineCommand.WORD_VIDEO),
            MINE_ALL_MSG: lambda: self._stateless.run(mine_intents.MineCommand.EPISODE),
            BOOKMARK_MSG: action(SessionController.toggle_bookmark),
            PROFILE_CYCLE_MSG: action(SessionController.cycle_profile),
            SIDEBAR_MSG: action(SessionController.toggle_sidebar),
            SUB_PICKER_MSG: action(SessionController.toggle_sub_picker),
            ANALYSIS_MSG: action(SessionController.toggle_analysis),
            PREVIEW_MSG: action(SessionController.replay_preview),
            PREVIEW_CLOSE_MSG: action(SessionController._hide_preview),
            SCROLL_UP_MSG: interaction(InteractionCommand.WHEEL_UP),
            SCROLL_DOWN_MSG: interaction(InteractionCommand.WHEEL_DOWN),
            TIP_UP_MSG: interaction(InteractionCommand.TOOLTIP_UP),
            TIP_DOWN_MSG: interaction(InteractionCommand.TOOLTIP_DOWN),
            TIP_CLOSE_MSG: interaction(InteractionCommand.TOOLTIP_BACK_OR_CLOSE),
            CLICK_MSG: interaction(InteractionCommand.CLICK),
            COPY_CLICK_MSG: interaction(InteractionCommand.COPY_UNDER_CURSOR),
            OVERLAY_TOGGLE_MSG: action(SessionController.toggle_overlay),
        }
        contributed = self._assembly.command_specs()
        contributed_names = {spec.name for spec in contributed}
        legacy = tuple(spec for spec in COMMAND_SPECS if spec.name not in contributed_names)
        return CommandExecutor(handlers, policy=CommandPolicy((*legacy, *contributed)))

    def _command_cue_state(self) -> CueCommandState:
        if not self._cue_retired:
            return CueCommandState.ACTIVE
        if self._cue_identity_ever_installed:
            return CueCommandState.RETIRED_AFTER_ACTIVE
        return CueCommandState.NEVER_INSTALLED

    def _handle(self, command: str | UserCommand) -> None:
        # Every saitenka script-message that reaches us (key- or mouse-driven) — the ground truth for
        # "did the keypress arrive". A shortcut that does nothing but never logs here never reached the
        # overlay (unregistered / shadowed by another mpv script or input.conf), vs. one that logs but
        # no-ops (a handler-side reason). Debug so it doesn't flood at info.
        if isinstance(command, str):
            command = UserCommand(command)
        log.debug("script-message: %s", command.name)
        result = self.commands.dispatch(
            command,
            cue_state=self._command_cue_state(),
            help_open=self.help.open,
        )
        self._publish_command_outcome(result)
        for coalesced in result.coalesced_events(command.coalesced_ids):
            self._publish_command_event(coalesced)
        if result.outcome == CommandOutcome.REJECTED:
            log.debug("script-message rejected (%s): %s", result.rejection, command.name)
        elif result.outcome == CommandOutcome.UNBOUND:
            log.error("script-message has no migration binding: %s", command.name)
        elif result.outcome == CommandOutcome.FAILED:
            log.error("script-message failed (%s): %s", result.error_type, command.name)

    def _redraw_after_resize(self) -> None:
        """Re-lay everything the window size decides, after an `osd-dimensions` change.

        Was `_refresh_surfaces`, a tick stage that re-detected per tick the change the projection
        already publishes. The name went with the mechanism: this runs because a resize was
        observed, not because a tick came round.
        """
        if self.refresh_osd():
            if self.sub_text.strip():
                self.draw_subtitle()
            self.help_controller.redraw()
            self._draw_analysis()
            # row capacity changed, so the active row may need re-centring
            sidebar.follow(self.sidebar_view)

    def _apply_capabilities(self) -> None:
        if self._tts_capability is not None:
            if self._tts_capability.value is not None:
                self._tts_ok = bool(self._tts_capability.value)
            self._tts_capability.request()
        self.mining_controller.refresh_capability()

    def _finish_analysis(self, completion: EffectFinished) -> None:
        changed = analysis_overlay.finish(self.analysis, completion)
        if completion.outcome is not EffectOutcome.REJECTED:
            changed |= analysis_overlay.submit_pending(
                self.analysis, self._analysis_submit, self._finish_analysis
            )
        if changed:
            self._draw_analysis()

    def _request_interaction_metadata(self, request) -> bool:
        return self.tooltip_controller.request_metadata(request, self._finish_interaction_metadata)

    def _finish_interaction_metadata(self, completion: EffectFinished) -> None:
        self.tooltip_controller.finish_metadata(
            completion, self._tooltip_apply, self._finish_interaction_metadata
        )

    def _request_render_ahead(self, view: PopupView, direction: int) -> bool:
        return self.tooltip_controller.request_render_ahead(
            view,
            direction,
            generation=self.prefetch_state.gen,
            scale=self.tip_scale.raster,
            on_finished=self._finish_render_ahead,
        )

    def _request_engaged_tooltip(self, request: tooltip_engaged.EngagedRequest) -> bool:
        return self.tooltip_controller.request_engaged(
            request,
            generation=self.prefetch_state.gen,
            on_finished=self._finish_engaged_tooltip,
        )

    def _finish_engaged_tooltip(self, completion: EffectFinished) -> None:
        self.tooltip_controller.finish_engaged(
            completion,
            generation=self.prefetch_state.gen,
            apply_factory=self._tooltip_apply,
            on_finished=self._finish_engaged_tooltip,
        )

    def _finish_render_ahead(self, completion: EffectFinished) -> None:
        self.tooltip_controller.finish_render_ahead(
            completion,
            generation=self.prefetch_state.gen,
            ports=self.tip_ports,
            on_finished=self._finish_render_ahead,
        )

    def submit_subtitle_fetch(
        self,
        request: subtitle_modes.SubtitleFetchRequest,
        *,
        name: str,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        episode = self.episode
        self._subtitle_fetch_sequence += 1
        identity = (self._subtitle_fetch_sequence, name)
        force_select_revision = None
        if request.force_select:
            self._subtitle_force_select_revision += 1
            force_select_revision = self._subtitle_force_select_revision

        def finish(completion: EffectFinished) -> None:
            if (
                episode is not self.episode
                or self._stop.is_set()
                or (
                    force_select_revision is not None
                    and force_select_revision != self._subtitle_force_select_revision
                )
            ):
                return
            try:
                subtitle_modes.apply_fetch_result(
                    self.track_ports, subtitle_modes.finish_fetch(request, completion)
                )
            finally:
                if on_done is not None:
                    on_done()

        submitter = self._subtitle_fetch_submit
        if submitter is None:
            subtitle_modes.apply_fetch_result(
                self.track_ports, subtitle_modes.unavailable_fetch(request)
            )
            if on_done is not None:
                on_done()
            return
        submitter(
            owner=Owner.SUBTITLE,
            identity=identity,
            lane="subtitle-fetch",
            request=request,
            on_finished=finish,
        )

    def rebind_episode(self) -> None:
        sub_picker.close_picker(
            self._picker_store, self.interaction.picker_panel, self.lifecycle_surfaces
        )
        self._subtitle_force_select_revision += 1
        self._retire_episode()
        self.episode = EpisodeContext()

    def _retire_episode(self) -> None:
        """Retire every owner's per-episode facts in one turn.

        The container rebind below is leak-free by construction; the slots are session-lived and
        are not, so their reset has to be *named*. A routed session hands the reactor one event and
        it reaches every slice at once — the atomicity `EpisodeContext` gets from being one object.
        Without a reactor there is no turn to be atomic in, so each store reduces it in turn.
        """
        if self._playback_store.routed:
            self._playback_store.dispatch(events.EpisodeRetired())
            return
        self._playback_store.dispatch(events.EpisodeRetired())
        self._subtitle_tracks.dispatch(events.EpisodeRetired())
        self.tooltip_controller.retire_episode()
        self.translation_store.dispatch(events.EpisodeRetired())

    # --- run loop -----------------------------------------------------------------------------
    def current_media_path(self) -> Path | None:
        """mpv's current file as an absolute path (``path`` is verbatim what was loaded, so resolve a
        relative one against ``working-directory``). None when nothing is loaded. Used by the reactive
        re-slot and the eof advance to key the #100 sibling resolver off the real filesystem path."""
        raw = self.observed_property("path")
        if not raw:
            return None
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            wd = self.observed_property("working-directory")
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
        self._replace_subtitle_source(p, reason="file-loaded")
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
        self._settle_cue_observation()

    def _settle_interaction(self) -> None:
        """Everything that reconciles against the turn's outcome rather than a wall clock.

        These ran on a 40 Hz tick stage. They are per-*turn* concerns — a queue to top up, pixels
        to align with the hover and cue the turn just settled — so the drain boundary is where they
        belong, and a runtime with no ticks still runs them.

        A relocation, not yet a decomposition: each has a delta that really drives it (an
        annotation terminal, the pointer, `secondary-sub-text`), and splitting them out is the next
        contract. Every one is guarded by a cheap early return, so asking once per turn is nothing
        like the cost of asking forty times a second.
        """
        self._feed_episode_annotation()
        self._sync_mouse_capture()
        self.tooltip_controller.publish_pending(self.tip_ports)
        self._update_prefetch()
        if self.translation_visible() and self._secondary_text() != self._trans_text:
            self.draw_translation()
        # The active row tracks the loaded index, language and scorer as well as the cue, and those
        # change without a cue settling — so this is outside the early return below.
        sidebar.follow(self.sidebar_view)

    def _settle_cue_observation(self) -> None:
        """Reconcile at the batch boundary, not per delta. mpv splits one cue across sub-text,
        sub-start and sub-end observations; the projection publishes each (it sees one observation
        at a time and has no batch), so reconciling per delta would build the cue three times, twice
        against a half-updated identity. The drain is where the batch exists, so it coalesces."""
        self._settle_interaction()
        cue, self._pending_cue = self._pending_cue, None
        # A settle that decides to do nothing stays visible, but as a count: the drain runs at mpv's
        # observation rate, so a span per empty one was 39% of a trace file saying nothing.
        if cue is None:
            otel_metrics.record_cue_settle("no-observation")
            return
        before = self.sub_text
        with otel_metrics.traced("cue_reconcile", cue_revision=str(self.cue_revision)) as span:
            self._reconcile_sub_text(cue.text)
            settled = "adopted" if self.sub_text != before else "reinstalled"
            otel_metrics.record_cue_settle(settled, span)

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
            self._retire_cue_identity("connection-lost")
            return
        if isinstance(ev, ConnectionReplaced):
            self._on_ipc_reconnect()
            return
        if isinstance(ev, events.FileLoaded):
            self._on_file_loaded()
            return
        if isinstance(ev, events.PropertyObserved):
            self._observe_property(ev.name, ev.data)
            return
        if isinstance(ev, ConnectionReady):
            # Only reached without a reactor: a session that has one claims this, because learning
            # the transport is back is the whole of what the event means. Its twin cannot be
            # claimed — losing the transport also strands a cue identity.
            self._connection.observed(ev)
            return
        if isinstance(ev, UserCommand):
            self._perform_command(ev)
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
            self._on_property_change(ev)
        elif kind == "client-message":
            args = ev.get("args") or [""]
            name = args[0] if isinstance(args[0], str) else ""
            self._handle(UserCommand(name, tuple(args[1:])))

    def _run_user_command(self, effect: object) -> None:
        """Perform `RunUserCommand`."""
        assert isinstance(effect, RunUserCommand)
        self._perform_command(effect.command)

    def _perform_command(self, command: UserCommand) -> None:
        """Run one command, or refuse it because the transport is gone.

        The refusal is here and not in the reducer because it reads the connection feature's state,
        and a slice's features do not read each other. It is also the honest place: whether mpv can
        still be commanded is a fact about the moment of performance.
        """
        if not self._connection.current.ready:
            self._publish_command_event(
                CommandHandled(
                    command.name,
                    None,
                    CommandOutcome.REJECTED,
                    command_id=command.command_id,
                    reason=CommandReason.DISCONNECTED,
                )
            )
            return
        self._handle(command)

    def _publish_command_outcome(self, result: CommandExecution) -> None:
        self._publish_command_event(result.event())

    def _publish_command_event(self, event: CommandHandled) -> None:
        self.ipc.publish_legacy_command_outcome(event)

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

    def arm_deps_ready(self) -> bool:
        """Hand a finished background dep build to the session turn.

        Called from the build thread, so this must not touch reader state — arming a zero-delay
        deadline is the hop: the due event runs where every other effect does. The tick used to
        discover `_pending_deps` by looking; now the build says so.
        """

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.DEPS_READY, 0.0, self._apply_pending_deps
        )

    def arm_session_persist(self, seconds: float) -> None:
        """Keep an uninterrupted session durable.

        Watch time accrues at transitions, and a viewer who never pauses produces none — so without
        this a long session would hold everything in memory until close and lose it all to a crash.
        The due event re-arms, because durability is a standing obligation rather than one deadline.
        """

        def due() -> None:
            session_stats.accrue(
                self.episode.session_recorder,
                paused=bool(self.observed_property("pause")),
                language=self.subtitle_language,
            )
            self.arm_session_persist(seconds)

        self.lifecycle_timers.schedule(LifecycleTimerKind.SESSION_PERSIST, seconds, due)

    def arm_hover_deadline(self, kind, seconds: float, due) -> bool:
        """Arm one hover dwell deadline, superseding any earlier one of the same kind."""
        return self.lifecycle_timers.schedule(kind, seconds, due)

    def cancel_hover_deadline(self, kind) -> None:
        """Retire a dwell the cursor has moved off. The revision bump is the point — a due event
        already in flight has to be fenced, not merely unscheduled."""
        self.lifecycle_timers.cancel(kind)

    def hold_sidebar_scroll(self, seconds: float) -> bool:
        """Arm the deadline that releases the sidebar's manual-scroll hold, resuming auto-follow.

        The due event follows the active row rather than only clearing the flag: a hold that expires
        while the cue has not moved would otherwise leave the sidebar off-target until the next cue
        happened to arrive.
        """

        def released() -> None:
            self._sidebar_store.dispatch(events.SidebarHoldReleased())
            sidebar.follow(self.sidebar_view)

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.SIDEBAR_MANUAL_HOLD, seconds, released
        )

    def schedule_flash_expiry(self) -> bool:
        """Arm the deadline that ends a copy-flash pulse. A second flash supersedes the first,
        which `LifecycleTimers` fences by revision so only the latest due event lands."""

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.FLASH_EXPIRY,
            self.tooltip_controller.flash_seconds,
            self._flash_expired,
        )

    def _flash_expired(self) -> None:
        for decision in self.tooltip_controller.expire_pulse():
            if decision.overlay == NESTED_ID:
                self._render_nested_view()  # redraw without the highlight border
            elif decision.overlay == TIP_ID:
                self._render_tip_view()

    def _mark_interactive_ready(self) -> bool:
        """Announce interactive readiness once. True when this call published the fact."""
        if self._interactive_ready:
            return False
        if self._observing and self._playback.value("osd-dimensions") in (None, {}):
            return False
        self._interactive_ready = True
        connected_at = self.ipc.connected_at  # None until the transport has connected once
        with otel_metrics.traced(
            "startup.interactive_ready",
            cue_pending=str(self._sub_pending is not None).lower(),
            deps_pending=str(not self._dependencies_settled).lower(),
        ) as span:
            if connected_at is not None:
                span.set("since_ipc_ms", round((time.monotonic() - connected_at) * 1_000, 3))
            self.ipc.publish_runtime_event(StartupReady())
        return True

    def _apply_pending_deps(self) -> None:
        """Drain completed dependency bundles on the session turn."""
        self.profile_dependencies.drain()

    def _schedule_paused_nudge(self, ops_before: int) -> None:
        """An overlay changed while mpv is paused → schedule a re-flush next tick so mpv actually
        presents it (mpv #8172; see Overlay.repaint). Only when paused: playing frames present on
        their own, and re-adding every tick would be wasteful."""
        if self.ov.ops == ops_before or not self.observed_property("pause"):
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
        osd_ok = self.observed_property("osd-dimensions") not in (None, {})
        if bytes_read == 0 or not osd_ok:
            log.warning(
                "IPC looks dead %.0fs after start (bytes from mpv=%d, osd-dimensions=%s) — mpv's "
                "replies/events aren't reaching the overlay, so nothing will draw (Windows named-pipe "
                "read failure, or attached to a not-yet-ready mpv).",
                secs,
                bytes_read,
                "ok" if osd_ok else "None",
            )
        elif not self.sub_text:
            log.debug(
                "IPC alive %.0fs in (bytes=%d, osd-dimensions ok) but no subtitle text yet — normal if "
                "this section has no subs (e.g. an OP); the overlay will draw when a cue appears.",
                secs,
                bytes_read,
            )

    # --- subtitle navigation (instant render, then seek) --------------------------------------
    @property
    def nav_ports(self) -> subnav.NavPorts:
        """The seam `subnav` converted onto. `geometry` is read here, not probed for: it is a
        declared field, so a `getattr` fallback could only ever hide a rename as a silent
        geometry-off."""

        def geometry_hint(cue) -> None:
            self.episode.geometry_cue_hint = cue

        return subnav.NavPorts(
            episode=self.episode,
            geometry=self.native_geometry,
            get=self._get,
            cue_text=lambda: self.sub_text,
            cue_retired=lambda: self._cue_retired,
            draw_cue=self.set_subtitle,
            replace_source=self._replace_subtitle_source,
            invalidate=self.invalidate_analysis,
            open_settle=self.open_settle_window,
            retire_settle=self.retire_settle_window,
            warm_tokens=self.warm_episode_tokens,
            index_changed=lambda: sidebar.index_changed(self.sidebar_view),
            geometry_hint=geometry_hint,
        )

    def load_sub_index(self, path) -> None:
        subnav.load_sub_index(self.nav_ports, path)

    def seek_cue(self, effect: subtitle_intents.SeekCue) -> bool:
        """Carry out a navigation step, unless the cue it was decided against is gone.

        "Previous" is meaningless without a cue to be previous *to*, so a step that outlives its
        cue would seek from a baseline the user never saw. Dropping it is reported rather than
        silent: a navigation key that does nothing and says nothing is indistinguishable from a
        wedged runtime.
        """
        with otel_metrics.traced("sub_nav_identity") as span:
            span.set("delta", effect.delta)
            span.set("requested_for", effect.cue_revision)
            if effect.cue_revision != self.cue_revision:
                span.set("outcome", "superseded")
                return False
            span.set("outcome", "executed")
        self.subtitle_pipeline.invalidate()
        self._sub_nav(effect.delta)
        # Correlated: the instant render above already drew the target, so what this write owes is
        # a terminal outcome — a refused seek that vanished into a discarded reply left the overlay
        # showing a cue the video never reached.
        send_correlated(self.ipc, "sub-seek", "sub-seek", str(effect.delta), owner=Owner.SUBTITLE)
        return True

    def _sub_nav(self, delta: int) -> bool:
        return subnav.sub_nav(self.nav_ports, delta)

    def _reconcile_sub_text(self, text: str) -> None:
        subnav.reconcile_sub_text(self.nav_ports, text)

    # --- progressive dep loading --------------------------------------------------------------
    @property
    def session_facts(self) -> session_runtime.SessionFacts:
        """What a noninteractive drive observes. A property, so it is not a debt row."""
        return session_runtime.SessionFacts(
            refresh_osd=self.refresh_osd,
            prop=self.observed_property,
            get=self._get,
            tokens=lambda: self.tokens,
            is_content_token=lambda token: self.profile_controller.tokenizer.is_content(token),
            osd_height=lambda: self.osd[1],
            painted=lambda: (
                self.lifecycle_surfaces.settled() and self.interaction_surfaces.settled()
            ),
        )

    @property
    def session_acts(self) -> session_runtime.SessionActs:
        """What a noninteractive drive performs — every one blocking or immediate by contract."""
        return session_runtime.SessionActs(
            drive_annotation_once=self._drive_annotation_once,
            prepare_subtitle=self.prepare_subtitle_blocking,
            prepare_hover=self.prepare_hover_blocking,
            mark_ready=self._mark_interactive_ready,
            scroll_tip=self.scroll_tip,
            setup_secondary=self.setup_secondary,
            toggle_translation=self.toggle_translation,
            mine_current=lambda: self._stateless.run(mine_intents.MineCommand.WORD),
            bulk_mine=lambda: self._stateless.run(mine_intents.MineCommand.EPISODE),
        )

    @property
    def deps_load(self) -> reader_deps.DepsLoad:
        """What starting a background dep load needs. A property, so it is not a debt row."""
        return reader_deps.DepsLoad(
            begin_loading=self._begin_loading,
            enable_async_annotation=self._enable_async_annotation,
            publish=self.profile_dependencies.publish,
            announce=self.arm_deps_ready,
        )

    @property
    def _profile_dependency_apply(self) -> reader_deps.ProfileDependencyApply:
        return reader_deps.ProfileDependencyApply(
            load_ports=lambda: self.deps_load,
            selected_profile=lambda: self.profile_controller.profile,
            select_mining=self.mining_controller.select_mining_spec,
            retire_current=self._retire_profile_dependencies,
            stop_loading=self._stop_loading,
            install=self._install_collaborators,
            arrived=self._dependencies_arrived,
        )

    def _begin_loading(self) -> None:
        """Plain subs plus the spinner until the deps land."""
        self._loading = True
        self._schedule_loading_frame(delay_s=0.0)

    def configure_profiles(
        self,
        profiles,
        *,
        dependency_builder_for,
        mining_spec_for,
        dict_scoper=None,
        base_slang: str = "ja,jpn,jp",
    ) -> None:
        """Install profile-aware collaborator factories at the composition boundary."""
        self.profile_dependencies.configure(dependency_builder_for, mining_spec_for)
        self.profile_controller.configure_cycle(
            profiles,
            dict_scoper,
            base_slang=base_slang,
            environment=ProfileEnvironment(self.profile_dependencies.select),
        )

    def _retire_profile_dependencies(self) -> None:
        self.scorer = None
        self._dependencies_changed()

    def load_deps_async(self, cfg: dict, build=None, *, prebuilt=None) -> None:
        self.profile_dependencies.load(cfg, build, prebuilt=prebuilt)

    def _apply_deps(self, deps: reader_deps.DependencyBundle) -> None:
        self.profile_dependencies.accept(deps)

    def _stop_loading(self) -> None:
        """Plain-subs mode is over: spinner frame, spinner overlay, and the previous deck's probe."""

        self._loading = False
        self.lifecycle_timers.cancel(LifecycleTimerKind.LOADING_FRAME)
        self.lifecycle_surfaces.remove(OverlayId.LOADING)
        self.mining_controller.close_capability()

    def _install_collaborators(self, deps: reader_deps.DependencyBundle) -> None:
        """Swap in what the build produced, and probe the deck it came with."""
        self.scorer = deps.scorer
        self.profile_controller.replace_dictionary_set(deps.dictionaries)
        if deps.mining is None:
            self.mining_controller.clear_mining_target(deps.identity)
        else:
            self.mining_controller.publish_mining_target(deps.mining)

    def _dependencies_arrived(self) -> None:
        """Everything that has to hear about a new vocabulary, in the order it has to hear it."""
        self.invalidate_analysis(vocabulary_changed=True)
        self._dependencies_changed()
        self.start_prefetch()  # prefetch can spin up now (no-op while dict_set is still None)
        self.warm_episode_tokens()  # deps landed after the index built → warm this episode's cues
        self._announce_runtime()  # workers are up — the banner can carry the real count (once)

    def _draw_loading(self) -> None:
        reader_deps.draw_loading(self.lifecycle_surfaces, self._load_frame)
        self._load_frame += 1

    def _schedule_loading_frame(self, *, delay_s: float) -> bool:

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.LOADING_FRAME,
            delay_s,
            self._loading_frame_due,
        )

    def _loading_frame_due(self) -> None:
        if not self._loading or self.profile_dependencies.ready:
            return
        self._draw_loading()
        self._schedule_loading_frame(delay_s=0.08)

    def _announce_runtime(self) -> None:
        """Print the runtime banner exactly once, from wherever prefetch actually finishes starting
        (sync: construction; async: apply_deps after start_prefetch). Reports the LIVE worker count — the old
        run()-time print always showed 0 because async deps hadn't spawned the workers yet."""
        if self._runtime_announced:
            return
        self._runtime_announced = True
        mode = "free-threaded (GIL off)" if gil_disabled() else "GIL"
        console_log.info("runtime: %s · %d prefetch worker(s)", mode, self.prefetch_state.workers)

    @property
    def session_entry(self) -> SessionEntry:
        """This reader as run mode's entry point: the demo runtime, and the loop."""
        return SessionEntry(
            runtime=SessionRuntime(self.session_facts, self.session_acts, self.ipc), run=self.run
        )

    def run(self) -> None:
        """Bring the session up phase by phase, then hand the thread to the loop.

        Announced rather than performed here: each phase's steps are the runtime's, registered as
        setup participants at construction. What survives on this side is the *order* — announcing
        `OBSERVERS` before `RENDER_SPACE` would seed geometry against dimensions nobody has read —
        and the fallback for a session with no runtime, which is what a screenshot capture and most
        unit tests are.
        """
        with otel_metrics.traced("startup.reader_setup"):
            for phase in events.StartPhase:
                self._announce_start(phase)
        # In run/attach the deps (and thus the prefetch lane) load ASYNC — dict_set is still None here,
        # so construction-time start_prefetch was a no-op and the worker count is 0. Defer the banner to when
        # prefetch actually starts (apply_deps → _announce_runtime); only announce now on the sync path
        # (deps already present, e.g. a demo/screenshot run) where apply_deps is never called.
        if self.profile_controller.dict_set is not None:
            self._announce_runtime()
        loop = self.ipc.session_loop
        if loop is None:
            # No gateway, so no mailbox and no timer heap to wait under: a session that is not one
            # (a screenshot capture, most unit tests) drives the same turn off the buffered wire.
            alive = True

            def step(timeout: float | None) -> None:
                nonlocal alive
                alive = self.pump(timeout)

            SessionRunner(step).run_until(lambda: not alive or self._stop.is_set())
            return
        loop.run(self.pump, until=self._stop.is_set)

    def request_stop(self) -> None:
        """Ask the loop to finish, from any thread.

        Setting the flag is not enough on its own: the loop observes it between steps, and a step
        blocks — with nothing armed, indefinitely. The wake is what makes the flag observable.
        """
        self._stop.set()  # the workers do no IPC, so signalling them is race-free
        self.ipc.wake_session_runtime()

    def _on_ipc_reconnect(self) -> None:
        self.subtitle_pipeline.connection_replaced(self.subtitle_target())

    def close(self) -> CloseLedger:
        """Tear the session down. Every participant runs even if an earlier one raises.

        A declared table rather than a statement sequence, so the runtime can take participants
        over one at a time as each duty migrates. Step bodies are lambdas *by contract*: the
        migration checker attributes a call to its enclosing `FunctionDef` and `Lambda` does not
        open one, so per-duty evidence and its pairwise `order:` constraints survive here — and
        would not survive being factored into a nested `def`.
        """

        self._build_close_participants()
        self.mining_controller.invalidate()
        ledger = CloseLedger()
        ledger.run(
            (
                # Each phase is announced *before* the steps it may replace, because only the
                # announcement can say whether anything performed them: a session with a gateway
                # but no reactor registers every participant and runs none of them.
                CloseStep(
                    "phase:capabilities", lambda: self._announce_close(ClosePhase.CAPABILITIES)
                ),
                *self._fallback_steps(CAPABILITY_PARTICIPANTS, ClosePhase.CAPABILITIES),
                # The runtime's own close participants: it announces, the session reducer emits
                # their effects. Delivered rather than published — the session loop has stopped,
                # and draining here would run a full domain turn against half-closed collaborators.
                CloseStep("runtime-close", lambda: self._announce_close(ClosePhase.PARTICIPANTS)),
                *self._fallback_steps(INTERACTION_WORK_PARTICIPANTS, ClosePhase.PARTICIPANTS),
                # A detached mpv must never outlive us still routing clicks here.
                CloseStep(
                    "mouse-capture",
                    lambda: self._mouse.release(),
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.PARTICIPANTS),
                        lambda: self._mouse,
                    ),
                ),
                CloseStep("phase:lanes", lambda: self._announce_close(ClosePhase.LANES)),
                # The order between these is the contract either way — it is the one
                # `WORKER_LANE_PARTICIPANTS` declares.
                *self._fallback_steps(WORKER_LANE_PARTICIPANTS, ClosePhase.LANES),
                # No refresh may land after the provider closes, nor a settle deadline outlive it.
                CloseStep("geometry-refresh", lambda: self.retire_geometry_refresh()),
                CloseStep("settle-window", lambda: self.retire_settle_window()),
                CloseStep("phase:rendering", lambda: self._announce_close(ClosePhase.RENDERING)),
                # A step each, so a failure in one isolates — the guarantee `_retire` reproduces
                # on the migrated path.
                CloseStep(
                    "subtitle-deactivate",
                    lambda: self.subtitle_pipeline.deactivate(self.subtitle_target()),
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.RENDERING),
                        lambda: self.subtitle_pipeline,
                    ),
                ),
                CloseStep(
                    "subtitle-clear",
                    self._clear_subtitle_pixels,
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.RENDERING),
                        lambda: self.native_geometry,
                    ),
                ),
                CloseStep(
                    "subtitle-close",
                    self._close_subtitle_raster,
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.RENDERING),
                        lambda: self.subtitle_pipeline,
                    ),
                ),
                CloseStep("phase:stores", lambda: self._announce_close(ClosePhase.STORES)),
                CloseStep(
                    "session-stats",
                    lambda: self._report_session(self.finish_session_stats()),
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.STORES), lambda: self.session
                    ),
                ),
                CloseStep(
                    "backlog-store",
                    self._close_backlog_store,
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.STORES),
                        lambda: self.session.backlog_store,
                    ),
                ),
                CloseStep(
                    "mined-store",
                    self.mining_controller.close_store,
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.STORES),
                        lambda: self.mining_controller,
                    ),
                ),
                CloseStep("lifecycle-timers", lambda: self.lifecycle_timers.close()),
                # The `SURFACES` phase, announced here rather than with the participants: every
                # render lane above has drained, so nothing can present again. Announcing it at
                # `PARTICIPANTS` would strip the overlays ~30 steps early, while a lane could
                # still add one.
                CloseStep("lifecycle-surfaces", lambda: self._retire_surfaces()),
                # Closed by the `SURFACES` phase above when a runtime owns it — this is the
                # fallback for a SessionController that has none, and must stay after the removes it carries.
                CloseStep(
                    "transport",
                    lambda: self.ov.close(),
                    fallback_after(
                        lambda: self._phase_performed(ClosePhase.SURFACES), lambda: self.ov
                    ),
                ),
                # Disarm what `PROCESS` armed. Process-global and session-lived: `run` turns it on
                # because *this* thread is the render loop, and a session that ends without turning
                # it back off leaves the next one in the same interpreter tripping on a loop that
                # is no longer anybody's. Last of the raster steps, so nothing above it is relaxed.
                CloseStep("render-guard", self._release_main_render),
                # Per-session scratch dir, once nothing can still write to it.
                CloseStep("temporary-artifacts", lambda: self._retire_artifacts()),
                # Last, because it is the session's terminal transition: the reactor rejects new
                # work and closes the mailbox, so nothing above may still need to publish — including
                # `_retire_artifacts`, which announces ARTIFACTS through it. A SessionController with no runtime
                # gets False and the step is a no-op.
                CloseStep("session-runtime", lambda: self.ipc.close_session_runtime()),
            )
        )
        report = ledger.report()
        if report is not None:
            log.warning("%s", report)
        return ledger

    def _step_for(self, phase: events.StartPhase) -> Callable[[], None]:
        """Bind one phase's step late: it reads collaborators the constructor is still building."""

        def run() -> None:
            self._startup_steps[phase]()

        return run

    def _announce_start(self, phase: events.StartPhase) -> None:
        """Tell the runtime setup reached `phase`, or run that phase's steps ourselves.

        Delivered rather than published for `_announce_close`'s reason inverted: the session loop
        has not started, so a published event would sit in the mailbox until the first pump — after
        the observers it is supposed to install.
        """
        if not self.ipc.deliver_runtime_event(events.SessionStarting(phase)):
            self._startup_steps[phase]()

    def _guard_main_render(self) -> None:
        from saitenka.render.banded import guard_main_render
        from saitenka.version import overlay_version

        # First line of every session's log: pins WHICH build actually drew (both run + attach reach
        # here). `doctor` reads it back to catch a stale attach process — an mpv left open across an
        # editable reinstall keeps its old modules until relaunched (see doctor.check_stale_overlay).
        log.info("saitenka overlay %s starting", overlay_version())
        # this IS the render loop — native rasterisation must run on a worker
        guard_main_render(on=True)

    def _start_observing_traced(self) -> None:
        with otel_metrics.traced("startup.reader_setup.observers"):
            self.start_observing()  # event-driven property reads from here on

    def _register_keybinds_traced(self) -> None:
        with otel_metrics.traced("startup.reader_setup.keybinds"):
            self._register_keybinds()

    def _seed_collaborators(self) -> None:
        self.mining_controller.request_seed()
        self.arm_capability_refresh()

    def _open_session_history(self) -> None:
        session_stats.start(
            self.episode,
            enabled=self.options.stats.enabled,
            path=lambda: self.observed_property("path"),
            arm=self.arm_session_persist,
        )

    def _attach_diagnostics(self) -> None:

        telemetry.set_gauge_provider(self._telemetry_gauges)  # no-op unless telemetry is configured
        self.lifecycle_timers.schedule(
            LifecycleTimerKind.STARTUP_HEALTH, 8.0, self._check_startup_health
        )

    def _fallback_steps(self, names: tuple[str, ...], phase: ClosePhase) -> tuple[CloseStep, ...]:
        """One `CloseStep` per participant, skipped when the runtime retires it instead.

        A step each rather than one for the group, so a failure in one isolates — the guarantee
        `_retire` reproduces on the migrated path.
        """
        return tuple(
            CloseStep(
                name,
                self._participant_for(name),
                fallback_after(lambda: self._phase_performed(phase), lambda: self),
            )
            for name in names
        )

    def _participant_for(self, name: str) -> Callable[[], None]:
        """Bind one close participant late: the table it reads is built in `close`."""

        def run() -> None:
            self._close_participants[name]()

        return run

    def _close_tts_capability(self) -> None:
        if self._tts_capability is not None:
            self._tts_capability.close()

    def _close_anki_capability(self) -> None:
        self.mining_controller.close_capability()

    def _close_annotation(self) -> None:
        if self._annotation is not None:
            self._annotation.close()

    def _lane_remaining(self) -> float:
        """What is left of the lane budget. Zero once it is spent, never negative."""
        return max(0.0, self._lane_deadline - time.monotonic())

    def _build_close_participants(self) -> None:
        """The steps behind `CloseCapabilityActors`, `CancelInteractionWork` and `CloseWorkerLanes`.

        Built in `close` rather than at construction because half of them read collaborators that
        are installed afterwards, and because the lane budget starts when the capabilities are down
        — computing it at construction would spend the whole window on the session.
        """

        close_lane = self.ipc.close_runtime_job_lane

        def lane(name: str) -> Callable[[], object]:
            return lambda: close_lane(name, self._lane_remaining())

        def _close_render_pool() -> None:
            """Retire the shared render pool once every lane above has cancelled its work.

            `wait=False`: the in-flight rasters poll `should_cancel` and the lanes have already set
            it, so they land on their own — waiting here would spend the close budget on work whose
            pixels nobody will see. Dropping the queue is the point; the interpreter's atexit join
            is what turns a leftover raster into a process that outlives mpv.
            """
            from saitenka.parallel import shutdown_shared_executor

            shutdown_shared_executor(wait=False)

        def start_lane_budget() -> None:
            # Armed here, not at table-build time: the 2s budget starts once the capabilities are
            # down, and computing it earlier would silently spend that window on their teardown.
            self.request_stop()
            self._lane_deadline = time.monotonic() + 2.0

        self._close_participants = dict(
            zip(
                CAPABILITY_PARTICIPANTS + INTERACTION_WORK_PARTICIPANTS + WORKER_LANE_PARTICIPANTS,
                (
                    self._close_tts_capability,
                    self._close_anki_capability,
                    self.tooltip_controller.cancel_jobs,
                    self.tooltip_controller.close_metadata,
                    start_lane_budget,
                    lane("subtitle-fetch"),
                    lane("subtitle-picker"),
                    lane(GEOMETRY_LANE),
                    self._close_annotation,
                    lane("cue-annotation"),
                    self.tooltip_controller.close_render_ahead,
                    lane("tooltip-render-ahead"),
                    self.tooltip_controller.close_engaged,
                    lane("tooltip-engaged"),
                    lambda: prefetch.close(self.prefetch_state),
                    lane("speculative-prefetch"),
                    lambda: mask_atlas_startup.close(self._mask_atlas_startup),
                    lane("mask-atlas-startup"),
                    lambda: mask_atlas_startup.uninstall(self.session.render_cache),
                    _close_render_pool,
                    # The unconstrained tail. Each one's state was already retired by an earlier
                    # phase — the probes at CAPABILITIES, the metadata at PARTICIPANTS, the mined
                    # seed by the generation bump `close` opens with — so what is left is the
                    # workers, and cancelling them here is what keeps one from running on into the
                    # store and anki closes two phases below.
                    lane("capabilities"),
                    lane("interaction-metadata"),
                    lane("mined-seed"),
                    lane("episode-analysis"),
                ),
                strict=True,
            )
        )

    def _release_main_render(self) -> None:
        from saitenka.render.banded import guard_main_render

        guard_main_render(on=False)

    def _phase_performed(self, phase: ClosePhase) -> bool:
        """Whether the runtime performed `phase`'s steps, so this session's own must not."""
        return self._runtime_close_phases.get(phase, False)

    def _announce_close(self, phase: ClosePhase, scratch: str | None = None) -> bool:
        """Tell the runtime the close sequence reached `phase`. False when no runtime owns us.

        Delivered rather than published: the session loop has stopped, so a published event would
        sit in the mailbox, and draining here would run a full domain turn against half-closed
        collaborators.
        """
        performed = self.ipc.deliver_runtime_event(SessionClosing(phase, scratch))
        self._runtime_close_phases[phase] = performed
        return performed

    def _retire_surfaces(self) -> None:
        """Announce the `SURFACES` phase, or close them ourselves.

        Same fallback shape as `_retire_artifacts`, and for the same reason: a `SessionController` with no
        runtime still built the surfaces, so somebody has to remove them.

        Gated on the *registration*, not on the announcement's return: `announce` reports only that
        a reactor saw the event, not that anything performed the effect. A session with a reactor
        but no registered resource would take the True and leak the overlays.
        """
        if self._runtime_owns_surfaces and self._announce_close(ClosePhase.SURFACES):
            return
        self.lifecycle_surfaces.close()

    def _retire_artifacts(self) -> None:
        """Hand the scratch dir to the runtime's `ARTIFACTS` phase, or remove it ourselves.

        The fallback is not what keeps this unmigrated — every migrated close duty has one, because
        a `SessionController` with no gateway still built the thing. What makes the gate sound here is that
        `RemoveSessionArtifacts` carries its own path: a reactor that *saw* the event can always
        perform it, which is the one case where `announce`'s return answers the question asked.
        """
        self.mining_controller.retire_artifacts(
            lambda path: self._announce_close(ClosePhase.ARTIFACTS, path)
        )

    def finish_session_stats(self) -> str | None:
        """Close the current episode's row and retire the recorder. Idempotent."""
        recorder, self.episode.session_recorder = self.episode.session_recorder, None
        return session_stats.finish(recorder, self.analysis.current)

    def _clear_subtitle_pixels(self) -> None:
        """The native path clears through the pipeline; the legacy path has nothing to clear."""
        if self.native_geometry is not None:
            self.subtitle_pipeline.clear(self.lifecycle_surfaces, self.ipc)

    def _close_subtitle_raster(self) -> None:
        """Whichever of the provider and the pipeline owns the raster closes it, never both."""
        if self.native_geometry is not None:
            self.native_geometry.close()
        else:
            self.subtitle_pipeline.close()

    def _close_backlog_store(self) -> None:
        if self.session.backlog_store is not None:
            self.session.backlog_store.close()

    def _report_session(self, summary: str | None) -> None:
        if summary and self.options.stats.summary:
            console_log.info("session: %s", summary)
