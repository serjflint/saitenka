"""The MVP reader loop: mpv subtitle → my overlay → hover → dictionary tooltip.

Polls mpv over IPC (no Lua): reads ``sub-text`` (native subs hidden) and ``mouse-pos``, draws the
subtitle as overlay #1 with per-word hitboxes, and on hover draws the looked-up entry as overlay #2
near the word. Both overlays live in mpv's own OSD surface → fullscreen-safe.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from concurrent.futures import Future

from saitenka import otel_metrics
from saitenka.app import (
    analysis_overlay,
    backlog,
    card_preview,
    cue_annotation,
    geometry_refresh,
    help_overlay,
    hover_metadata,
    hover_snapshot,
    mask_atlas_startup,
    mined_seed,
    mined_store,
    miner_ui,
    native_subtitles,
    nested_popup,
    popups,
    prefetch,
    reader_deps,
    session_stats,
    sidebar,
    sub_picker,
    subnav,
    subnav_settle,
    subtitle_intents,
    subtitle_modes,
    surfaces,
    telemetry,
    tooltip,
    tooltip_engaged,
    tooltip_raster,
    translation,
)
from saitenka.app.bindings import (
    ANALYSIS_MSG,
    ANNOTATION_MSG,
    BOOKMARK_MSG,
    CLICK_MSG,
    COPY_CLICK_MSG,
    COPY_LINE_MSG,
    COPY_MSG,
    HELP_CLOSE_MSG,
    HELP_NEXT_MSG,
    HELP_PREV_MSG,
    HELP_TOGGLE_MSG,
    HOVER_PAUSE_MSG,
    KANJI_MSG,
    MINE_ALL_MSG,
    MINE_MSG,
    MINE_VIDEO_MSG,
    MOUSE_SECTION,
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
)
from saitenka.app.config import ReaderOptions
from saitenka.app.languages import MAIN_LANG, SECOND_LANG
from saitenka.app.media import (
    copy_clipboard,
    tts_available,
)
from saitenka.app.miner import Miner, tag_slug
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.perf import gil_disabled
from saitenka.app.popups import Panel, PopupView
from saitenka.app.profiles import DEFAULT_PROFILE, Profile, effective_slang
from saitenka.app.reader_context import (
    Delegated,
    EpisodeContext,
    InteractionContext,
    RenderCacheState,
    SessionContext,
)
from saitenka.app.runtime import (
    CommandExecution,
    CommandOutcome,
    CueCommandState,
    LegacyCommandBinding,
    LegacyCommandExecutor,
    LegacyPickerRepeatGuard,
    TickPipeline,
    TickStage,
)
from saitenka.app.subtitle_pipeline import (
    GEOMETRY_LANE,
    CurrentSubtitleRenderer,
    SubtitleGeometryWorker,
    SubtitleModeCoordinator,
)
from saitenka.app.subtitle_pipeline import (
    configure_runtime_job as configure_geometry_lane,
)
from saitenka.app.subtitle_render import NativeVisibleRenderer, NullRenderer, SubtitleRenderer
from saitenka.app.toast import render_toast
from saitenka.app.token_cache import TokenCache, TokenizedCue
from saitenka.app.tokenize import Token
from saitenka.app.tokenizer import Tokenizer, get_tokenizer
from saitenka.mpvio.gateway import register_observer_set
from saitenka.mpvio.osd import Overlay
from saitenka.runtime import (
    CommandHandled,
    CommandReason,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EffectFinished,
    EffectOutcome,
    Owner,
    UserCommand,
    playback,
)
from saitenka.runtime.playback import PlaybackProjection, PlaybackState
from saitenka.subtitles import Cue, CueIndex

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka.app.card_preview import PreviewData
    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.loading import StartupHintLease
    from saitenka.app.render_cache import RenderCache
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.panel import Freq
    from saitenka.subtitles import GeometryBackend

log = logging.getLogger(__name__)

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
    "options/sub-ass-vsfilter-aspect-compat",
    "options/sub-ass-style-overrides",
    "options/sub-font-provider",
    "options/embeddedfonts",
    "options/sub-fonts-dir",
    "eof-reached",  # #100: rising edge drives auto-advance (only when advance_hook is installed)
)
# Which observations are geometry inputs, which retire the cue identity, and which render space
# they revise all live in saitenka/runtime/playback.py — the sole interpreter.
# The one-panel crisp path snaps the display scale to this bucket so mpv's osd-dimensions wobble
# reuses cached native bands instead of re-rastering (see Reader._raster_scale).
_SETTLE_TIMER = "subtitle:navigation-settle"
_GEOMETRY_REFRESH_TIMER = "subtitle:geometry-refresh"
_SCALE_BUCKET = 0.05

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


class Reader:
    """Owns the reader loop (see module docstring): subtitle draw → hover hit-test → tooltip → mine."""

    # Episode-tier state (app/reader_context.py) exposed under its historical field names so the ~15
    # call-site modules keep working while they migrate onto ``reader.episode.*`` (#30 lifetime split);
    # rebinding ``self.episode`` (#100 re-slot) resets all of it in one move, leak-free by construction.
    jp_sid = Delegated[int | None]("episode.subtitle", "jp_sid")
    en_sid = Delegated[int | None]("episode.subtitle", "en_sid")
    subtitle_language = Delegated[subtitle_modes.Language]("episode.subtitle", "language")
    subtitle_slang = Delegated[str]("episode.subtitle", "slang")
    _sub_index = Delegated[CueIndex | None]("episode", "sub_index")
    _nav_idx = Delegated[int]("episode", "nav_idx")
    _sub_settle = Delegated[subnav_settle.SettleWindow]("episode", "sub_settle")
    _nav_prev_text = Delegated[str]("episode", "nav_prev_text")
    _nav_provisional_cue_counted = Delegated[bool]("episode", "nav_provisional_cue_counted")
    _session_recorder = Delegated[session_stats.SessionRecorder | None](
        "episode", "session_recorder"
    )
    # Help overlay state (app/help_overlay.py HelpState) under its historical flat names.
    _help_open = Delegated[bool]("help", "open")
    _help_page = Delegated[int]("help", "page")
    # Interaction-tier state (app/reader_context.py InteractionContext) under historical flat names.
    _translate_on = Delegated[bool]("interaction", "translate_on")
    _trans_text = Delegated[str | None]("interaction", "trans_text")
    _translation_secondary_sid = Delegated[int | None](
        "episode.subtitle", "translation_secondary_sid"
    )
    # Prefetch runtime state (app/prefetch.py PrefetchState) under its historical flat names.
    _head_built = Delegated[int]("prefetch_state", "head_built")
    _prefetch_gen = Delegated[int]("prefetch_state", "gen")
    _prefetch_key = Delegated[tuple[str, bool] | None]("prefetch_state", "key")
    # Session-lifetime state (app/reader_context.py SessionContext) under its historical flat names;
    # the render-cache / mask-atlas cluster is migrated directly onto ``reader.session.render_cache.*``.
    _mined = Delegated[set[str]]("session", "mined")
    _anki_cache = Delegated[tuple[float, bool]]("session", "anki_cache")
    _backlog_store = Delegated[backlog.BacklogStore | None]("session", "backlog_store")
    _mined_store = Delegated[mined_store.MinedCardStore | None]("session", "mined_store")
    # Base-tooltip runtime state + hover FSM (app/popups.py TooltipState) under its historical flat
    # names — the hot interaction-scoped cluster, woven through tooltip.py / nested_popup.py / prefetch.
    _paused_by_tip = Delegated[bool]("tip", "paused_by_tip")
    _hide_pending = Delegated[bool]("tip", "hide_pending")
    # The base tooltip's PopupView + its fields, delegated through the dotted "tip.view" path so the
    # historical flat names keep resolving while base and nested share one view type + blit machinery.
    _tip_view = Delegated[PopupView]("tip", "view")
    _tip_rect = Delegated[tuple | None]("tip.view", "rect")
    _tip_scroll = Delegated[int]("tip.view", "scroll")
    _tip_view_h = Delegated[int]("tip.view", "view_h")
    _tip_xy = Delegated[tuple[int, int]]("tip.view", "xy")
    _tip_state = Delegated[Panel | None]("tip.view", "state")
    _tip_key = Delegated[tooltip.PanelKey | None]("tip.view", "key")
    _tip_nav = Delegated[list]("tip", "tip_nav")
    _nest = Delegated[PopupView]("tip", "nest")
    _scan_target = Delegated[str | None]("tip", "scan_target")
    _word_target = Delegated[int | None]("tip", "word_target")
    _last_mouse = Delegated[tuple[float, float]]("tip", "last_mouse")
    _flash_oid = Delegated[int | None]("tip", "flash_oid")
    _hover_reading = Delegated[str]("tip", "hover_reading")
    _hover_terms = Delegated[tuple[str, ...]]("tip", "hover_terms")
    _hover_span = Delegated[tuple[int, int] | None]("tip", "hover_span")
    _kanji_index = Delegated[int]("tip", "kanji_index")
    _tip_keys_bound = Delegated[bool]("tip", "tip_keys_bound")
    _tip_tok = Delegated[Token | None]("tip", "tip_tok")
    _tip_inflected = Delegated[str | None]("tip", "tip_inflected")
    _crisp_miss = Delegated[str]("tip.view", "crisp_miss")
    _crisp_pending = Delegated[bool]("tip.view", "crisp_pending")
    _tip_show_cold = Delegated[bool]("tip", "tip_show_cold")
    _panel_cache = Delegated[OrderedDict]("tip", "panel_cache")

    def __init__(  # noqa: PLR0913, PLR0917 -- optional backend is the native boundary seam
        self,
        ipc: MpvIPC,
        scorer=None,
        anki=None,
        mine_cfg=None,
        dict_set=None,
        options: ReaderOptions | None = None,
        renderer: SubtitleRenderer | NullRenderer | None = None,
        geometry_backend: GeometryBackend | None = None,
        profile: Profile | None = None,
        startup_hint_lease: StartupHintLease | None = None,
        tokenizer_warm: Future[None] | None = None,
        tts_ok: bool | None = None,  # noqa: FBT001 -- tri-state capability snapshot
        runtime_submit=None,
        **legacy_kw,
    ):
        """``options`` is the canonical grouped-knobs object (see app/config.py; a new knob is one
        dataclass field). Legacy exploded kwargs (``mine_key=…``, ``tip_max_frac=…``) are still
        accepted and routed onto the groups; unknown names raise TypeError. ``renderer`` is the
        subtitle-draw strategy (app/subtitle_render.py) — pass ``NullRenderer()`` to run headless."""
        o = options or ReaderOptions()
        if legacy_kw:
            o = o.with_overrides(**legacy_kw)
        self.options = o
        # Episode-lifetime state; the Delegated shims above expose its fields as ``reader.<field>``.
        # A file change rebinds this (#100 re-slot) — see app/reader_context.py.
        self.episode = EpisodeContext()
        self.interaction = InteractionContext()  # hover/tooltip/reveal-scoped state
        self.ui_scale = max(0.75, min(2.0, float(o.panels.scale)))
        self.ipc = ipc
        self._startup_hint_lease = startup_hint_lease
        self._interactive_ready = False
        self._connection_ready = True
        # Supplied by composition (`create_reader`), never probed off `ipc`: which egress the
        # overlay uses is a wiring decision, not something to infer from a collaborator's methods.
        self.ov = Overlay(ipc, id_base=o.overlay_id_base, runtime_submit=runtime_submit)
        from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
        from saitenka.app.lifecycle_timers import LifecycleTimers

        self.lifecycle_surfaces = LifecycleSurfaces(self.ov)
        self.lifecycle_timers = LifecycleTimers(ipc)
        self._analysis_submit = analysis_overlay.configure_runtime_job(ipc)
        self._subtitle_fetch_submit = subtitle_modes.configure_runtime_job(ipc)
        self._subtitle_fetch_sequence = 0
        self._subtitle_force_select_revision = 0
        self._sub_picker_submit = sub_picker.configure_runtime_job(ipc)
        current_renderer: CurrentSubtitleRenderer = renderer or SubtitleRenderer()
        self.native_geometry: native_subtitles.NativeSubtitleGeometry | None = None
        if o.subtitle_geometry.native_visible and renderer is None:
            current_renderer = NativeVisibleRenderer()
        # No provider is chosen here. Which implementation renders geometry is a composition
        # decision (`reader_factory._geometry_backend`); a host that picks its own provider cannot
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
                lookahead=o.subtitle_geometry.lookahead,
            )
        self.sub_size_override = o.tooltip.sub_size
        self.bottom_margin_frac = o.tooltip.bottom_margin_frac
        # Alpha (0–255) of the translucent box behind the rendered subtitle; 0 = no box (fully see-through).
        self.sub_bg_opacity = max(0, min(255, o.tooltip.sub_background_opacity))
        self.scorer = scorer  # app.scoring.Scorer | None — per-word coloring
        self.styles: list | None = None
        self.anki = anki  # app.anki.Anki | None — enables one-key mining
        self.mine_cfg = mine_cfg
        self.dict_set = dict_set  # app.dictionary.DictionarySet | None — multi-dict tooltip
        # Progressive startup: deps loaded on a background thread, injected on the main thread by the
        # poll loop (see load_deps_async / _apply_deps). Until then, subs render plain + a spinner shows.
        self._pending_deps: dict | None = None
        self._mined_seed_generation = 0
        self._mined_seed_inflight = False
        self._mined_seed_done = False
        self._mined_seed_failures = 0
        self._mined_seed_next_due = 0.0
        self._mined_generation = 0
        from saitenka.app.interaction_jobs import InteractionJobs

        self._interaction_jobs = InteractionJobs()
        self._interaction_metadata = hover_metadata.InteractionMetadataState()
        self._interaction_metadata_submit = hover_metadata.configure_runtime_job(ipc)
        self._hover_mined = False
        self._hover_group_mined: tuple[bool, ...] = ()
        self._loading = False
        self._load_frame = 0
        self._miner = Miner(self)  # mining flow (app/miner.py)
        self.mine_key = o.keys.mine_key
        self.mine_video_key = o.keys.mine_video_key
        self.mine_all_key = o.keys.mine_all_key
        self.translate_key = o.keys.translate_key
        self.overlay_toggle_key = o.keys.overlay_toggle_key
        self.subtitle_language_key = o.keys.subtitle_language_key
        self.subtitle_mark_jp_key = o.keys.subtitle_mark_jp_key
        self.bookmark_key = o.keys.bookmark_key
        self.sidebar_key = o.keys.sidebar_key
        self.analysis_key = o.keys.analysis_key
        self.annotation_key = o.keys.annotation_key
        self.help_key = o.keys.help_key
        self.profile_cycle_key = o.keys.profile_cycle_key
        self.subtitle_retry_key = o.keys.subtitle_retry_key
        self.sub_picker_key = o.keys.sub_picker_key
        self.preview_key = o.keys.preview_key
        self.hover_pause_key = o.keys.hover_pause_key
        self.play_audio = o.mining.play_audio
        self.show_preview = o.mining.show_preview  # auto-pop the card-preview panel after a mine
        # Interactive sessions publish this optional subprocess probe later; deterministic
        # demo/screenshot assembly supplies it synchronously through ReaderServices.
        self._tts_ok = bool(tts_ok)
        from saitenka.app.capabilities import CapabilityProbe, configure_runtime_jobs

        self._capability_submit = configure_runtime_jobs(ipc)
        self._mined_seed_submit = mined_seed.configure_runtime_job(ipc)

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
        self._anki_capability: CapabilityProbe | None = None
        # subtitle navigation keys (configurable; defaults match SUB_NAV_DEFAULTS)
        self.sub_prev_key = o.keys.sub_prev_key  # Alt+LEFT  → sub-seek -1 (previous line)
        self.sub_next_key = o.keys.sub_next_key  # Alt+RIGHT → sub-seek  1 (next line)
        self.sub_replay_key = o.keys.sub_replay_key  # Alt+DOWN  → sub-seek  0 (replay current)
        self.tip_max_frac = o.tooltip.tip_max_frac  # BASE tooltip viewport ≤ this frac of the video
        self.nested_max_frac = o.tooltip.nested_max_frac  # nested (scan) popup viewport frac cap
        self.pause_on_tooltip = o.tooltip.pause_on_tooltip  # auto-pause mpv while a tooltip shows
        if o.tooltip.annotation_mode not in {"full", "hover"}:
            raise ValueError(f"unknown annotation mode: {o.tooltip.annotation_mode!r}")
        self.annotation_mode: subtitle_intents.AnnotationMode = o.tooltip.annotation_mode
        self._annotation_hover = False
        # Visual-only: draw the kanji panel's big headword in the numbered stroke-order font. Set here
        # (the shared Reader init) so both the run and attach seams get it from one place; a pure render
        # flag threaded onto the kanji Entry, never gating what's looked up or the panel-cache identity.
        self.kanji_stroke_order = o.tooltip.kanji_stroke_order
        self.hide_delay = o.tooltip.hide_delay  # tooltip linger after the cursor leaves the word
        self.flash_secs = o.tooltip.flash_secs  # "copied" highlight border pulse duration
        self.panel_cache_max = (
            o.tooltip.panel_cache_max
        )  # LRU cap on cached rendered tooltip panels
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
        self._tip_scale_override = o.tooltip.tip_scale  # >0 fixes _tip_display_scale (see config)
        # Base-tooltip runtime state + hover FSM (app/popups.py TooltipState). The Delegated shims below
        # keep the historical ``reader._tip_*``/``_nest``/``_scan_*``/``_hover_*``/``_flash_*``/
        # ``_panel_cache`` names so the hover FSM and its tests are untouched (#30 lifetime split).
        self.tip = popups.TooltipState()
        from saitenka.render.layout_backend import backend_label, resolve_backend

        # Resolve the tooltip geometry backend ONCE (probes the optional taffylite wheel behind the
        # import chokepoint; missing → default). Threaded to every Panel.from_rows so all popups agree.
        self.layout_backend = resolve_backend(o.tooltip.layout_engine)
        self.layout_engine = backend_label(
            self.layout_backend
        )  # effective tag for logs + span attrs
        # Positive, truthful signal in the report bundle (overlay.log): the EFFECTIVE backend vs what was
        # requested — a "taffy" request landing on 'default' is a silent-fallback flag.
        log.info("layout backend: %s (requested %r)", self.layout_engine, o.tooltip.layout_engine)
        self.max_bulk = o.mining.max_bulk  # cap on words mined in one "mine all" bulk action
        self.anki_ok_ttl = (
            o.mining.anki_ok_ttl
        )  # seconds an AnkiConnect reachability check is cached
        self.anki_ping_timeout = o.mining.anki_ping_timeout  # reachability ping timeout
        # background prefetch: render the paused line's tooltips ahead of the mouse. The worker does
        # CPU-only work (lookup + render + BGRA), NEVER touches the mpv IPC socket (main thread only).
        self.prefetch = o.prefetch
        self.poll_interval = o.perf.poll_interval  # main loop tick
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
        self._engaged_tooltip = tooltip_engaged.EngagedState()
        self._engaged_tooltip_backend = tooltip_engaged.ReaderEngagedBackend(self)
        self._engaged_tooltip_submit = tooltip_engaged.configure_runtime_job(
            ipc, self._engaged_tooltip_backend
        )
        self._render_ahead = tooltip_raster.RenderAheadState()
        self._render_ahead_submit = tooltip_raster.configure_runtime_job(ipc)
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
        # Active reading profile (#254) — the identity layer (main/second language codes + tokenizer
        # name), held as swappable state for a live switch (D8). Distinct from the subtitle *role*
        # sentinels the primary/secondary state machine compares. Default = today's JP profile.
        self.profile: Profile = profile or DEFAULT_PROFILE
        self.langs = self.profile.langs  # concrete language codes; consumers key identity off this
        self._apply_font_mode()  # Latin-first font order for a non-JP profile (nicer Latin typography)
        # The ordered cycle the live switcher (#254 D8) rotates through; a single entry (the default
        # path) makes cycle_profile a no-op. cli installs the real cycle via set_profile_cycle.
        self.profiles: tuple[Profile, ...] = (self.profile,)
        self._profile_idx = 0
        # The raw CLI/config slang the switcher falls back to for a profile with no slang of its own
        # (the JP default), so cycling back to it re-selects the original track. Set by set_profile_cycle.
        self._profile_base_slang = "ja,jpn,jp"
        # Optional dict re-scoper (#254 W3): profile → its scoped DictionarySet, installed by the CLI
        # alongside the cycle so a live switch re-scopes dictionaries too, not just the tokenizer.
        self._dict_scoper: Callable[[Profile], DictionarySet | None] | None = None
        # Active tokenizer strategy (app/tokenizer.py) — the language-dependent morphology seam, selected
        # by the profile's tokenizer name. A profile switch (#254) swaps it via use_tokenizer.
        self.tokenizer: Tokenizer = get_tokenizer(self.profile.tokenizer)
        self._cache_lock = (
            threading.Lock()
        )  # tiny lock: only the cache dict mutation (build is lock-free)
        self._mouse_in = False  # cursor over the video window — an engagement signal
        self._hit_test_tick = 0  # samples the OTel hit-test histogram every _HIT_TEST_SAMPLE_EVERY
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
        self._last_announced_sid: int | None = None
        self.sidebar = sidebar.SidebarState()
        self.sub_picker = sub_picker.PickerState()
        self._sub_picker_lister: Callable[[str], tuple] | None = None
        self.analysis = analysis_overlay.AnalysisState()
        self.help = help_overlay.HelpState()
        # Last-mined card's media + on-screen preview panel (app/card_preview.py PreviewState); the
        # Delegated shims below keep the historical ``reader._last_*``/``_preview_*`` names working.
        self.preview = card_preview.PreviewState()
        # Forced mouse-section state (see _sync_mouse_capture).
        self._mouse_section_defined = False
        self._mouse_captured = False
        self._mouse_reassert_at = 0.0
        # Tooltip scan/switch dwell caps (config) — the runtime dwell state lives on ``self.tip``.
        self.scan_delay = o.tooltip.scan_delay
        self.hover_switch_delay = o.tooltip.hover_switch_delay
        self._tmp = Path(tempfile.mkdtemp(prefix="saitenka-mine-"))
        # Event-driven property state (observe_property); empty + off until run() calls
        # start_observing(), so direct get_property keeps working for tests / pre-run paths.
        self._observing = False
        # Sole interpreter of raw mpv observations (saitenka/runtime/playback.py): it owns the
        # latest values, the explicit source/track/render-space revisions, and the decision that a
        # given observation conflicts with the installed cue identity.
        self._projection = PlaybackProjection()
        self._playback = PlaybackState()
        self._geometry_refresh = geometry_refresh.RefreshWindow()
        #: Latest cue identity observed this drain, reconciled once at the batch boundary.
        self._pending_cue: playback.ObservedCue | None = None
        self._ass_full_probe_dirty = True
        # #100 auto-advance: run mode installs a re-slot callback; the presence of the hook IS the
        # opt-in (never set under attach, so SyncPlay-managed playback never advances). `_eof_handled`
        # makes the eof-reached edge one-shot per file (re-armed when a new file clears eof-reached).
        self.advance_hook: Callable[[], bool] | None = None
        self._eof_handled = False
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
        self._geometry_cue_hint: Cue | None = None
        self.hover = -1
        self._nudge_pending = (
            False  # a draw happened while paused → re-flush the OSD next tick (#8172)
        )
        self.commands = self._build_command_router()
        self.tick_pipeline = self._build_tick_pipeline()
        self.start_prefetch()
        from saitenka.app.paths import cache_dir

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
        return hover_snapshot.snapshot(self)

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
    def tip_width(self) -> int:
        # FIXED to the 1920×1080 REFERENCE (16:9), NOT the live OSD, so the render cache is
        # resolution-independent (a 1080p prewarm hits at any playback res). The tooltip is a
        # VIDEO-OVERLAY element: like the subtitle it scales with the vertical viewport (_tip_display_scale
        # = osd_h/REF_H) at upload, NOT with the app-chrome ui_scale — so displayed width ≈ 0.59 × osd_h,
        # calculated from the vertical viewport (narrow on an ultra-wide, unlike an osd_WIDTH formula).
        # Its fonts are theme scale 1.0 (panel_rows gets no ui_scale), so width must stay 1.0 too.
        return int(min(prefetch.REF_W * 0.36, 640))

    @property
    def _tip_display_scale(self) -> float:
        """Factor from the tooltip's REFERENCE render space to the live display: ``osd_h / REF_H`` — 1.0
        at 1080p, 2.0 at 4K. Applied to the composited BGRA at upload and inverted in the hit-test, so
        one 1080p-prewarmed render cache serves every resolution and the tooltip tracks the vertical
        viewport size."""
        if self._tip_scale_override > 0:  # [tooltip] tip_scale — a fixed cosmetic preference
            return self._tip_scale_override
        return self.osd[1] / prefetch.REF_H

    @property
    def _raster_scale(self) -> float:
        """The display scale SNAPPED to a 0.05 bucket — the scale the one-panel crisp path rasters,
        composites, AND inverts the hit-test at (all three must agree). Bucketing means mpv's osd-
        dimensions wobble (``osd_h`` ±few px → a jitter in the 3rd decimal) reuses cached native bands
        instead of re-rastering. Geometry is already scale-free, so this is a pure raster-cache concern.
        The tooltip rasters, composites, and hit-tests at this one bucketed value."""
        return round(self._tip_display_scale / _SCALE_BUCKET) * _SCALE_BUCKET

    @property
    def _tip_ref_h(self) -> int:
        """The tooltip's reference render height (``REF_H``) — the panel-content coordinate space (scroll
        amounts, viewport caps live here, not in OSD pixels; the display scale maps it to the screen)."""
        return prefetch.REF_H

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
    def _get(self, prop):
        return self.ipc.command("get_property", prop).get("data")

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
                self._playback = self._projection.seed(self._playback, name, data)
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

    def _prop(self, name: str) -> Any:
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
        projected = self._projection.observe(self._playback, name, data)
        self._playback = projected.state
        for delta in projected.deltas:
            self._apply_playback_delta(delta)

    def _probe_ass_full(self) -> None:
        """Resolve mpv's authored-ASS capability once per file. Driven by `AuthoredCueStale`, which
        the projection publishes on the same observation that invalidated the cached probe."""
        if self.native_geometry is None or not self._ass_full_probe_dirty:
            return
        if self.native_geometry.ass_full_capability.value == "unknown":
            reply = self.ipc.command("get_property", "sub-text/ass-full")
            self._playback = self._projection.seed(
                self._playback, "sub-text/ass-full", reply.get("data")
            )
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
                self.native_geometry.set_source(None, reader=self)
            else:
                self.subtitle_pipeline.invalidate()
            subtitle_modes.on_primary_changed(self, delta.sid)
        elif isinstance(delta, playback.SubtitleTimingChanged):
            if self.native_geometry is not None:
                self.native_geometry.record_clock_change(self)
        elif isinstance(delta, playback.GeometryInputChanged) and self.native_geometry is not None:
            self._arm_geometry_refresh()

    def _install_cue_identity(self, identity: cue_annotation.CueIdentity) -> None:
        """Bind the cue identity in both owners: the annotation state and the projection, which
        decides which later observation conflicts with it."""
        self._current_cue_identity = identity
        self._cue_retired = False
        self._playback = self._projection.install(
            self._playback,
            start=identity.observed_start,
            end=identity.observed_end,
        )

    # --- coalesced geometry refresh (WP4.4) ----------------------------------------------------
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

        schedule = getattr(self.ipc, "schedule_runtime_timer", None)
        if schedule is None or not schedule(
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
            self.native_geometry.refresh(self)

    def retire_geometry_refresh(self) -> None:
        """Drop a pending refresh; the source or track it was armed for is gone."""
        if self._geometry_refresh.armed is None:
            return
        self._geometry_refresh = self._geometry_refresh.retire()
        cancel = getattr(self.ipc, "cancel_runtime_timer", None)
        if cancel is not None:
            cancel(_GEOMETRY_REFRESH_TIMER)

    # --- subtitle navigation settle window (WP4.5) --------------------------------------------
    def open_settle_window(self) -> None:
        """Absorb mpv's mid-seek transients until the seek lands or the named deadline is due."""
        window = self._sub_settle.begin()
        self._sub_settle = window
        identity = window.identity

        def due(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._settle_due(identity)

        schedule = getattr(self.ipc, "schedule_runtime_timer", None)
        if schedule is None:
            # No runtime timer port (tests, pre-run): never open a window we cannot retire.
            self._sub_settle = window.retire()
            return
        if not schedule(
            owner=Owner.SUBTITLE,
            identity=identity,
            timer=_SETTLE_TIMER,
            due_at=time.monotonic() + subnav_settle.SETTLE_SECONDS,
            on_finished=due,
        ):
            self._sub_settle = window.retire()

    def _settle_due(self, identity: subnav_settle.NavigationSettleDue) -> None:
        self._sub_settle = self._sub_settle.due(identity)

    def retire_settle_window(self) -> None:
        """Close the window and cancel its deadline; safe to call when none is open."""
        if not self._sub_settle.open:
            return
        self._sub_settle = self._sub_settle.retire()
        cancel = getattr(self.ipc, "cancel_runtime_timer", None)
        if cancel is not None:
            cancel(_SETTLE_TIMER)

    def _replace_subtitle_source(self, path: object = None, *, reason: str) -> None:
        self.retire_settle_window()
        """A new authored subtitle source is live: revise it in the projection (which every cue
        identity is derived from) and retire the identity the old source produced."""
        self._playback = self._projection.source_replaced(self._playback, path).state
        self._retire_cue_identity(reason)

    def _clear_cue_identity(self) -> None:
        """Drop the installed identity in both owners; the projection then stops treating later
        sub-start/sub-end observations as conflicts."""
        self._cue_retired = True
        self._current_cue_identity = None
        self._playback, _deltas = self._projection.retire(
            self._playback, playback.RetireReason.CUE_TEXT
        )

    def _retire_cue_identity(self, reason: str) -> None:
        if self._cue_retired:
            self._clear_cue_identity()
            return
        log.debug("cue interaction retired: %s", reason)
        self._clear_cue_identity()
        self._teardown_tip()
        self.hover = -1
        self.lines, self.tokens, self.styles, self.boxes = [], [], None, []

    def refresh_osd(self) -> bool:
        d = self._prop("osd-dimensions") or {}
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
        vop = probe.get("video-out-params") or {}
        span_attrs = {
            "reason": reason,
            "tip_scale": f"{self._tip_display_scale:.4f}",
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
    def _teardown_tip(self) -> None:
        """Tear down the hover stack unconditionally: hide TIP_ID/NESTED_ID, reset all tooltip
        state, and release any _paused_by_tip. Called by set_hover(-1) AND set_subtitle so that
        a cue change while a tooltip is showing always clears it via the real path — avoiding the
        early-return in set_hover (index == self.hover) that would otherwise short-circuit teardown
        when hover is already -1 but the tip is still on screen."""
        self._interaction_jobs.cancel_all()
        hide = getattr(self.ov, "hide_interactive", self.ov.hide)
        hide(TIP_ID)
        self._hide_nested()
        self._tip_rect = None
        self._tip_state = None
        self._tip_key = None
        self._tip_tok = self._tip_inflected = None
        self._tip_nav = []  # drop any link-navigation history with the tooltip
        self._hover_reading = ""
        self._hover_terms = ()
        self._hover_span = None
        self._kanji_index = 0
        self._unbind_tip_keys()
        if self._paused_by_tip:
            submit = getattr(self.ipc, "command_async", self.ipc.command)
            submit("set_property", "pause", False)  # noqa: FBT003
            self._paused_by_tip = False
        self._sync_auto_translation()

    def set_subtitle(
        self,
        text: str,
        *,
        revise_session_cue: bool = False,
        provisional_navigation: bool = False,
    ) -> None:
        if provisional_navigation:
            self._nav_provisional_cue_counted = False
        # Per-cue breadcrumb (low frequency): correlates mpv's sub-text change with the overlay draw +
        # paused-state in the report — the mpv-log-vs-overlay-log gap the paused-OSD bug lives in.
        log.debug("sub-text change: %d chars, paused=%s", len(text.strip()), self._prop("pause"))
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
        self.subtitle_pipeline.cue_changed(self, nonempty=bool(text.strip()))
        # Tear down the hover stack via the shared path BEFORE mutating sub_text/hover so that
        # TIP_ID/NESTED_ID are hidden, _tip_rect/_tip_state/_tip_key/_nest are reset, and any
        # _paused_by_tip is released.  We cannot rely on set_hover(-1) here because its
        # early-return (index == self.hover) would skip teardown if hover is already -1 but
        # tip state is present (e.g. _show_tooltip was called directly without set_hover).
        with otel_metrics.traced("teardown_tip"):
            self._teardown_tip()
        self.hover = -1
        self._annotation_hover = False
        self.sub_text = text
        # Invariant 13: the projection owns which cue is current, so a Reader-side decision about
        # it has to reach the projection too — otherwise the next changed cue fact reconciles mpv's
        # stale text back over this one.
        self._playback = self._projection.cue_replaced(self._playback, text)
        self._clear_cue_identity()
        self._sub_pending = None  # any cue change abandons a still-pending upgrade for the old cue
        self._annotation_degraded = False
        self._nav_idx = -1  # any external cause of a cue change invalidates the nav chaining hint
        with otel_metrics.traced("hide_preview"):
            self._hide_preview()  # a new cue → dismiss the last card preview
        if not text.strip():
            self.lines, self.tokens, self.boxes = [], [], []
            if self.native_geometry is not None:
                self.native_geometry.mark_empty()
            self.subtitle_pipeline.clear(self)
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
            self._install_cue_identity(self._annotation_identity(self._cue_norm(text)))
            self._cue_identity_ever_installed = True
            self._draw_subtitle()
            return
        # honour explicit line breaks (\n, ASS \N); tokenize each source line separately
        norm = self._cue_norm(text)
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
            self._draw_subtitle()
            return
        else:
            self._apply_tokenized_cue(self._tokenize_cue(norm))
        # A cue must appear at its cue time even when annotation isn't ready. While the dictionaries
        # are still loading the tokenization can't be complete (no compound merge, no coloring), so
        # draw the cue PLAIN now; reader_deps re-renders it in place once deps land. A cache hit or a
        # deps-ready miss tokenizes synchronously (fast) and annotates immediately.
        self._sub_pending = norm if self.dict_set is None else None
        if self.native_geometry is not None:
            self.boxes = []
            self.native_geometry.schedule(self)
        self._draw_subtitle()

    def _record_session_cue(self, text: str, *, revise: bool, provisional_navigation: bool) -> None:
        recorder = self._session_recorder
        if recorder is None:
            return
        identity = (
            self.subtitle_language,
            self._prop("sub-start"),
            self._prop("sub-end"),
            text,
        )
        if revise:
            recorder.revise_cue(identity)
            return
        counted = recorder.record_cue(identity)
        if provisional_navigation:
            self._nav_provisional_cue_counted = counted

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
        norm = self._cue_norm(text)
        cue = self._annotation.resolve(
            self._annotation_key(norm),
            self._annotation_inputs(norm),
            priority=cue_annotation.AnnotationPriority.CURRENT,
            drive=self._drive_annotation_once,
        )
        self.token_cache.put(norm, cue)
        self.set_subtitle(text)

    def _drive_annotation_once(self, timeout: float | None) -> None:
        picker_guard = LegacyPickerRepeatGuard()
        for event in self.ipc.drain_events(timeout):
            self._drain_event(event, picker_guard)

    def _annotation_identity(self, norm: str) -> cue_annotation.CueIdentity:
        return cue_annotation.CueIdentity(
            self._playback.media.source.value,
            self._prop("sid"),
            self.subtitle_language,
            norm,
            self._prop("sub-start"),
            self._prop("sub-end"),
            self._nav_idx if self._nav_idx >= 0 else None,
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
            self.tokenizer,
            getattr(self.dict_set, "terms_exist", None),
            self.scorer,
            len(getattr(self.dict_set, "dicts", ())),
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
        self._teardown_tip()
        self.hover = -1
        self.lines, self.tokens, self.styles, self.boxes = [], [], None, []
        norm = self._cue_norm(self.sub_text)
        self._sub_pending = norm
        if self.native_geometry is not None:
            self.native_geometry.invalidate(self)
        self._schedule_current_annotation(norm)
        self._draw_subtitle()

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
            self.native_geometry.schedule(self)
        self._draw_subtitle()
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

    @staticmethod
    def _cue_norm(text: str) -> str:
        """The token-cache key for a cue: mpv's sub-text with ASS/CR line breaks normalized to \\n.
        The SAME transform for a live cue and a warmed one, so an episode-prefetched line is a hit."""
        return text.replace("\\N", "\n").replace("\r", "")

    def warm_episode_tokens(self) -> None:
        """Kick off the background full-episode token warm (no-op without prefetch + a dict + index)."""
        prefetch.warm_episode_tokens(self)

    def _start_episode_annotation(self, index: CueIndex) -> None:
        self._annotation_episode_index = index
        self._annotation_episode_cursor = 0
        self._feed_episode_annotation()

    def _feed_episode_annotation(self) -> None:
        coordinator = self._annotation
        index = self._annotation_episode_index
        if coordinator is None or index is None or self._sub_index is not index:
            return
        while coordinator.pending_count() < 4 and self._annotation_episode_cursor < len(index.cues):
            cue = index.cues[self._annotation_episode_cursor]
            self._annotation_episode_cursor += 1
            norm = self._cue_norm(cue.text)
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
        exists = getattr(self.dict_set, "terms_exist", None)
        with otel_metrics.traced("tokenize_line", chars=str(len(norm))):
            raw = (self.tokenizer.tokenize(ln) for ln in norm.split("\n") if ln.strip())
            lines = [self.tokenizer.merge_dict_compounds(t, exists) if exists else t for t in raw]
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

    def use_tokenizer(self, tokenizer: Tokenizer) -> None:
        """Swap the active tokenizer strategy (a profile switch, #254). Clears the token cache —
        cached tokenizations are strategy-specific, so a stale entry would leak the old language's
        segmentation into the new profile."""
        self.tokenizer = tokenizer
        if self.native_geometry is not None:
            self.native_geometry.invalidate(self)
        else:
            self.subtitle_pipeline.invalidate()
        self.token_cache.clear()

    def set_profile_cycle(
        self,
        profiles: Sequence[Profile],
        dict_scoper: Callable[[Profile], DictionarySet | None] | None = None,
        *,
        base_slang: str = "ja,jpn,jp",
    ) -> None:
        """Install the ordered profile cycle the live switcher rotates through (cli wiring, #254 D8). An
        empty or single-entry cycle keeps the switcher inert; the cursor starts at the active profile.
        ``dict_scoper`` (optional) maps a profile → its scoped ``DictionarySet`` so a live switch
        re-scopes dictionaries too (#254 W3); ``None`` keeps the current dict set across a cycle.
        ``base_slang`` is the raw CLI/config slang a slang-less profile (the JP default) falls back to,
        so a cycle re-selects that profile's own subtitle track."""
        self.profiles = tuple(profiles) or (self.profile,)
        self._dict_scoper = dict_scoper
        self._profile_base_slang = base_slang
        self._profile_idx = next(
            (i for i, p in enumerate(self.profiles) if p.name == self.profile.name), 0
        )

    def _apply_font_mode(self) -> None:
        """Lead the font fallback chain with the font best suited to the active profile's script (a
        European profile → NotoSans; JP → the universal default). One source of truth for both __init__
        and cycle_profile so the two can't drift."""
        from saitenka import fonts
        from saitenka.app.profiles import primary_font_for

        fonts.set_primary_font(primary_font_for(self.profile.langs.main))

    def cycle_profile(self) -> None:
        """Cycle the active reading profile among the configured ``[profiles.*]`` at runtime (#254 D8).
        A no-op with a single configured profile (the default path). Resolves the new tokenizer FULLY
        before touching any live state, so an unresolvable profile leaves the old one intact (atomic
        revert). On success re-resolves the reader's identity — tokenizer, ``langs`` (which gates
        providers), ``profile`` — clears+re-arms the token warm (the cache-clear-vs-warm race is closed
        by the cache generation gate), re-selects the subtitle track for the new profile's language, and
        flashes the new profile. Re-selecting the track is what makes the cycle a FULL switch: the new
        language's track lands in the target slot (colored + scanned), instead of the engine reading the
        old language's track — or the profile-blind role machine filing a manual pick as the secondary."""
        if len(self.profiles) <= 1:
            return  # nothing to switch to — inert on the default single-profile path
        idx = (self._profile_idx + 1) % len(self.profiles)
        new = self.profiles[idx]
        # Resolve BOTH the tokenizer and the re-scoped dict set FULLY before mutating any live state, so
        # an unresolvable profile (bad tokenizer / DB error) leaves the old one intact (atomic revert).
        try:
            tok = get_tokenizer(new.tokenizer)
        except ValueError:
            self._toast(f"profile {new.name!r}: unknown tokenizer {new.tokenizer!r}", "warn")
            return
        rescope = self._dict_scoper is not None
        new_dict_set = self.dict_set
        if rescope:
            try:
                new_dict_set = self._dict_scoper(new)  # type: ignore[misc]  # guarded by `rescope`
            except Exception:  # noqa: BLE001 — a rescope failure must not kill the switch; keep old dicts
                self._toast(f"profile {new.name!r}: dictionary rescope failed", "warn")
                return
        self._profile_idx = idx
        self.profile = new
        self.langs = new.langs  # provider gating + identity read live off this
        self._apply_font_mode()  # switch Latin-first font order with the profile
        self.use_tokenizer(
            tok
        )  # swaps the strategy AND clears the token cache (bumps its generation)
        if rescope:
            self.dict_set = new_dict_set  # #254 W3 — the new profile's scoped dictionaries, live
            # Force the memoised render-cache signature to recompute off the NEW dict set, else composed
            # tooltips from the old profile's dicts would keep being served under the stale signature.
            self.session.render_cache.config_sig = None
        self._warmed_index = None  # re-arm the episode warm under the new tokenizer/generation
        if not self._switch_subtitle_track(effective_slang(new, self._profile_base_slang)):
            # Same track (or none for this language) — refresh the on-screen cue under the new tokenizer.
            # When the track DID switch, the new track's own sub-text event repaints, so re-tokenizing the
            # stale old-language cue under the new tokenizer would only flash garbage.
            self._retokenize_current_cue()
        self.warm_episode_tokens()
        self._toast(f"profile: {new.name} ({new.langs.main})")

    def _switch_subtitle_track(self, new_slang: str) -> bool:
        """Re-select the mpv subtitle track for the new profile's language via the SAME path launch uses
        (select_initial → configure_subtitle_mode → rebuild the cue index), so the target-language track
        lands in the target slot — colored, scanned, and nav/prefetch-indexed. Returns ``True`` when a
        track was switched. A missing target track keeps the current one and toasts (the file just has no
        track for that language); an unchanged slang is a no-op so the engine swap alone stands."""
        if new_slang == self.subtitle_slang:
            return False
        if not subtitle_modes.has_track_for_slang(self.ipc, new_slang):
            self._toast(f"profile {self.profile.name!r}: no {new_slang!r} subtitle track", "warn")
            return False
        startup = subtitle_modes.select_initial(self.ipc, new_slang)
        self.configure_subtitle_mode(startup, slang=new_slang)
        from saitenka.app.embedded_subs import build_sub_index_for_current_track

        self._sub_index = None  # the old track's index is wrong for the new one; rebuild from disk
        build_sub_index_for_current_track(self)
        return True

    def _retokenize_current_cue(self) -> None:
        """Re-render the on-screen cue under the freshly-swapped tokenizer — set_subtitle's tokenize
        path without its teardown/recording side effects. No-op when nothing's shown or the secondary
        (English) track is up (which never tokenizes)."""
        if not self.sub_text.strip() or self.subtitle_language == SECOND_LANG:
            return
        if self._annotation_async:
            self._retire_cue_identity("profile")
            norm = self._cue_norm(self.sub_text)
            self._install_cue_identity(self._annotation_identity(norm))
            self._sub_pending = norm
            self._annotation_degraded = False
            self._schedule_current_annotation(norm)
            self._draw_subtitle()
            return
        self._apply_tokenized_cue(self._tokenize_cue(self._cue_norm(self.sub_text)))
        if self.native_geometry is not None:
            self.native_geometry.refresh(self)
        self._draw_subtitle()

    def _draw_subtitle(self) -> None:
        self.subtitle_pipeline.draw_current(self)
        if self.native_geometry is not None:
            self.native_geometry.sync_pixel_owner(self)

    def _clear_native_interaction(self) -> None:
        self._teardown_tip()
        self.hover = -1
        self._hover_span = None
        self.boxes = []
        self.subtitle_pipeline.clear(self)

    def _degrade_native_subtitle_geometry(self) -> None:
        renderer = self.subtitle_pipeline.renderer
        ownership = getattr(renderer, "ownership_state", None)
        owner = getattr(getattr(ownership, "owner", None), "value", None)
        if owner != "legacy":
            self.boxes = []
        self.subtitle_pipeline.geometry_degraded(self)

    def _use_native_subtitle_renderer(self) -> bool:
        renderer = self.subtitle_pipeline.renderer
        return not isinstance(renderer, NativeVisibleRenderer) or renderer.use_native(self)

    def _native_ownership_undecided(self) -> bool:
        """True while a visibility assertion is in flight, so `_use_native_subtitle_renderer` said
        no for lack of an answer rather than because mpv refused. Publishing must wait, not degrade:
        the assertion's terminal re-drives the refresh."""
        renderer = self.subtitle_pipeline.renderer
        return isinstance(renderer, NativeVisibleRenderer) and renderer.assertion_in_flight

    # --- hover --------------------------------------------------------------------------------
    def _hit(self, mx: float, my: float) -> int:
        ox, oy = self.sub_origin
        for b in self.boxes:
            tok = self.tokens[b.index]
            if self.tokenizer.is_skippable(tok):
                continue
            if b.contains(mx - ox, my - oy):
                return b.index
        return -1

    @staticmethod
    def _in_rect(rect, x: float, y: float) -> bool:
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def _update_hover(self) -> None:
        if not getattr(self.ov, "visible", True) or surfaces.suppress_hover(self):
            return
        tooltip.update_hover(self)

    def set_hover(self, index: int) -> None:
        tooltip.set_hover(self, index)

    def prepare_hover_blocking(self, index: int) -> None:
        """Build the deterministic demo/screenshot hover before the event loop starts."""
        metadata_submit, engaged_submit = (
            self._interaction_metadata_submit,
            self._engaged_tooltip_submit,
        )
        self._interaction_metadata_submit = None
        self._engaged_tooltip_submit = None
        try:
            tooltip.set_hover(self, index)
        finally:
            self._interaction_metadata_submit = metadata_submit
            self._engaged_tooltip_submit = engaged_submit

    def set_annotation_hover(self, *, revealed: bool) -> None:
        target = bool(
            revealed
            and self.annotation_mode == "hover"
            and self.subtitle_language == MAIN_LANG
            and self.tokens
        )
        if target == self._annotation_hover:
            return
        self._annotation_hover = target
        self._draw_subtitle()

    def speak_hovered(self) -> None:
        tooltip.speak_hovered(self)

    def copy_hovered(self) -> None:
        tooltip.copy_hovered(self)

    def _copy_token(self, t) -> None:
        tooltip.copy_token(self, t)

    def copy_line(self) -> None:
        """Shift+C — copy the whole subtitle cue under the cursor (all its lines)."""
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.COPY_LINE)

    def _flash(self, oid: int) -> None:
        tooltip.flash(self, oid)

    def copy_click(self) -> None:
        tooltip.copy_click(self)

    def _hit_header_region(self, x: float, y: float, prect, xy, scroll: int, view_h: int) -> bool:
        return tooltip.hit_header_region(self, x, y, prect, xy, scroll, view_h)

    def _hit_header_add(self, x: float, y: float) -> bool:
        return tooltip.hit_header_add(self, x, y)

    def _hit_header_speaker(self, x: float, y: float) -> bool:
        return tooltip.hit_header_speaker(self, x, y)

    def _hit_nested_add(self, x: float, y: float) -> bool:
        return tooltip.hit_nested_add(self, x, y)

    def _hit_nested_speaker(self, x: float, y: float) -> bool:
        return tooltip.hit_nested_speaker(self, x, y)

    def on_click(self) -> None:
        if not self.ov.visible:
            return
        mp = self._get("mouse-pos") or {}
        surfaces.route_click(self, mp.get("x", -1), mp.get("y", -1))

    def _panel_key(
        self,
        tok,
        inflected,
        *,
        mined: bool = False,
        phrase: tuple[str, ...] = (),
        group_mined: tuple[bool, ...] | None = None,
    ) -> tooltip.PanelKey:
        return tooltip.panel_key(
            self,
            tok,
            inflected,
            mined=mined,
            phrase=phrase,
            group_mined=group_mined,
        )

    def _is_mined(self, tok) -> bool:
        return tooltip.is_mined(self, tok)

    def _anki_ok(self) -> bool:
        return tooltip.anki_ok(self)

    @staticmethod
    def _darken(rgba, f: float = tooltip.JLPT_DARKEN):
        return tooltip._darken(rgba, f)

    def _jlpt_pill(self, tok) -> Freq | None:
        return tooltip.jlpt_pill(self, tok)

    def _rareness_pill(self, tok) -> Freq | None:
        return tooltip.rareness_pill(self, tok)

    def _entry_for(self, tok, inflected):
        return tooltip.entry_for_tok(self, tok, inflected)

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
        return tooltip.panel_for(
            self,
            tok,
            inflected,
            min_h,
            mined=mined,
            nested=nested,
            extra_terms=extra_terms,
            group_mined=group_mined,
        )

    def _panel_cache_setdefault(self, key, st) -> Panel:
        return tooltip.panel_cache_setdefault(self, key, st)

    # --- persistent render cache (#149): seed a cold hover's first viewport from disk ----------
    def _render_cache(self) -> RenderCache | None:
        """The cross-session render cache, USED WHEN AVAILABLE: opened lazily only if a prebuilt
        ``render-cache.sqlite`` already exists (``saitenka prewarm`` builds it). ``None`` when opted out,
        no dict set, or no prebuilt cache — so a fresh install creates nothing and costs nothing."""
        rc = self.session.render_cache
        if not rc.cache_on or self.dict_set is None:
            return None
        if not rc.built:
            rc.built = True
            from saitenka.app.paths import cache_dir
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
        cap = self._tip_cap()
        ck = (self.tip_width, cap)
        if rc.config_sig is None or rc.sig_key != ck:
            from saitenka.app.render_cache import config_signature, dict_set_signature

            assert (
                self.dict_set is not None
            )  # _render_cache() gated on it before any caller reaches here
            rc.config_sig = config_signature(
                width=self.tip_width, cap=cap, dict_sig=dict_set_signature(self.dict_set)
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
        prefetch.start_prefetch(self)

    def _update_prefetch(self) -> None:
        generation = self._prefetch_gen
        prefetch.update_prefetch(self)
        if self._prefetch_gen != generation:
            self._cancel_engaged_tooltip()
            self._cancel_render_ahead()

    def _finish_speculative_prefetch(self, completion: EffectFinished) -> None:
        prefetch.finish(self.prefetch_state, completion, self._finish_speculative_prefetch)

    def _upcoming_cue_texts(self, n: int) -> list[str]:
        return prefetch.upcoming_cue_texts(self, n)

    def _inflected_surface(self, index: int) -> str:
        return self.tokenizer.inflected_in(self.tokens, index)

    def _telemetry_gauges(self) -> dict[str, float]:
        """Live cache-size gauges for the telemetry interval sampler (writer thread, ~1s cadence — NOT
        the hot path). ``panel_cache.bytes`` is the retained (compressed) on-heap footprint;
        ``dict_cache.size`` the decoded-entry count across every dictionary. Read under ``_cache_lock``
        so a concurrent prefetch job mutating the panel cache can't fault the iteration."""
        with self._cache_lock:
            panel_n = len(self._panel_cache)
            panel_bytes = sum(st.retained_nbytes for st in self._panel_cache.values())
        dict_n = self.dict_set.decoded_entry_count() if self.dict_set is not None else 0
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
        return prefetch.cap_for(self, frac)

    def _tip_cap(self) -> int:
        return prefetch.tip_cap(self)

    def _show_tooltip(self, index: int) -> None:
        tooltip.show_tooltip(self, index)

    def _place_panel(
        self, full_w: int, wx: float, wy: float, wh: float, view_h: int
    ) -> tuple[int, int]:
        return tooltip.place_panel(self, full_w, wx, wy, wh, view_h)

    def _blit_panel(self, panel, scroll: int, view_h: int, xy, oid: int):
        return tooltip.blit_panel(self, panel, scroll, view_h, xy, oid)

    def _bind_tip_keys(self) -> None:
        """Register the tooltip-scoped keys (idempotent — word switches must not re-bind)."""
        if self._tip_keys_bound:
            return
        self._tip_keys_bound = True
        if self._help_open:
            return
        for binding in active_bindings(self, "tooltip"):
            submit = getattr(self.ipc, "command_async", self.ipc.command)
            submit("keybind", binding.key, f"script-message {binding.spec.message}")

    def _unbind_tip_keys(self) -> None:
        """Neutralise the tooltip keys so a leaked bind can't fire ``tab-prev``/etc. when no tooltip is
        up. mpv has no unbind verb over IPC, and ``keybind KEY ""`` is REJECTED — it logs the noisy
        ``[input] Command name missing`` / ``Invalid command for key binding 'LEFT': ''`` triple (visible
        on the Windows console; silently on the mac log). Rebind to the valid no-op ``ignore`` instead:
        no error, and the key stops doing tooltip work while the popup is gone."""
        if not self._tip_keys_bound:
            return
        self._tip_keys_bound = False
        if self._help_open:
            return
        for binding in active_bindings(self, "tooltip"):
            submit = getattr(self.ipc, "command_async", self.ipc.command)
            submit("keybind", binding.key, "ignore")

    def _define_mouse_section(self) -> None:
        """Define (once) the FORCED mpv section for the ``mouse``-scoped bindings; once enabled it
        outranks other scripts' forced MBTN_LEFT (uosc/inputevent). Enabled per _sync_mouse_capture."""
        lines = [f"{b.key} script-message {b.spec.message}" for b in active_bindings(self, "mouse")]
        self._mouse_section_defined = bool(lines)
        if lines:
            self.ipc.command("define-section", MOUSE_SECTION, "\n".join(lines) + "\n", "force")

    def _wants_mouse_capture(self) -> bool:
        return surfaces.wants_mouse_capture(self)

    def _sync_mouse_capture(self) -> None:
        """Own clicks/wheel while a saitenka surface is up (re-asserting the forced section every 0.5s
        so a script re-forcing its own can't reclaim it), release it otherwise."""
        if not self._mouse_section_defined:
            return
        want = self._wants_mouse_capture()
        try:
            if want:
                now = time.monotonic()
                if not self._mouse_captured or now >= self._mouse_reassert_at:
                    self.ipc.command(
                        "enable-section", MOUSE_SECTION, "allow-hide-cursor+allow-vo-dragging"
                    )
                    self._mouse_captured, self._mouse_reassert_at = True, now + 0.5
            elif self._mouse_captured:
                self.ipc.command("disable-section", MOUSE_SECTION)
                self._mouse_captured = False
        except (OSError, ValueError):
            pass  # mpv went away mid-tick — poll_once will notice

    def _release_mouse_capture(self) -> None:
        """Drop the forced section on teardown so a detached mpv can't route clicks to a dead saitenka."""
        if not self._mouse_captured:
            return
        try:
            self.ipc.command("disable-section", MOUSE_SECTION)
        except (OSError, ValueError):
            pass
        self._mouse_captured = False

    def _render_tip_view(self) -> None:
        tooltip.render_tip_view(self)

    def _render_nested_view(self) -> None:
        tooltip.render_view(self, self._nest)

    def _scroll_tip(self, delta: int) -> None:
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
            tooltip.scroll_tip(self, delta)
            st = self._tip_state
            if st is not None:
                # Attribute a janky frame: bands rastered synchronously (render_ahead was behind) and
                # the panel's height. A warm frame is bands=0; the jank tail is the frames with bands>0.
                span.set("bands", st.last_frame_rasters)
                span.set("full_h", st.full_height)
                # Crisp health per scroll frame: the display scale (does it jitter mid-scroll?) and the
                # soft-fallback reason ("" = composited crisp) — so a soft run is attributable to a cause.
                span.set("scale", f"{self._tip_display_scale:.4f}")
                span.set("crisp_miss", self._crisp_miss or "n/a")

    def _scroll_nested(self, delta: int) -> None:
        tooltip.scroll_view(self, self._nest, delta)

    # --- nested scanning: hover a word INSIDE the tooltip → its own popup -----------------------
    def _scan_hit(self, mx: float, my: float):
        return tooltip.scan_hit(self, mx, my)

    def _show_nested(self, sb) -> None:
        nested_popup.show_nested(self, sb)

    def _open_nested(self, tok, inflected, wx: float, wy: float, wh: float, tail=None) -> None:
        nested_popup.open_nested(self, tok, inflected, nested_popup.Anchor(wx, wy, wh), tail)

    def _place_nested(
        self, st, key, token, word: str, wx: float, wy: float, wh: float, tail=None
    ) -> None:
        nested_popup.place_nested(self, st, key, token, word, nested_popup.Anchor(wx, wy, wh), tail)

    # --- clickable cross-reference links ---------------------------------------------------------
    def _tip_link_hit(self, mx: float, my: float):
        # Hit-test the panel actually DRAWN for the base tooltip (crisp native when shown, else reference)
        # so a clicked/hovered cross-reference link lands right despite native-vs-reference wrap drift.
        panel, scale, scroll = tooltip.hit_target(self, nested=False)
        return nested_popup.link_hit(mx, my, panel, self._tip_xy, scroll, scale=scale)

    def _nest_link_hit(self, mx: float, my: float):
        panel, scale, scroll = tooltip.hit_target(self, nested=True)
        return nested_popup.link_hit(mx, my, panel, self._nest.xy, scroll, scale=scale)

    def _open_link(self, lb, xy, scroll: int) -> None:
        nested_popup.open_link(self, lb, xy, scroll)

    def _navigate_tip(self, query: str) -> None:
        """Yomitan-style: a cross-reference clicked in the BASE tooltip replaces its content in place
        and pushes the previous view onto the back-stack (Esc/back returns)."""
        tooltip.navigate_tip(self, query)

    def _navigated_panel(self, query: str):
        """The read-only reference Panel for a nav target — built off the main thread by the engaged
        tooltip lane, so the seam lives on the Reader (no engaged-tooltip→tooltip import)."""
        return tooltip._navigated_panel(self, query)

    def _tip_close_or_back(self) -> None:
        """Esc while link-navigated steps back one entry; at the root (or a plain hovered word) it
        closes the tooltip — the browser-back-then-close feel Yomitan's history gives."""
        if not tooltip.tip_back(self):
            self.set_hover(-1)

    def _open_search(self, pattern: str, wx: float, wy: float, wh: float) -> None:
        nested_popup.open_search(self, pattern, wx, wy, wh)

    def _engaged_open_panel(self, source: str, query: str, *, mined: bool | None = None):
        """The (cached) panel for a clicked/keyed nested open — the shared builder the engaged-tooltip
        lane and session thread reach via the Reader seam. The worker
        passes ``mined`` (jamdict isn't worker-safe); the main thread lets it recompute."""
        return nested_popup._engaged_open_panel(self, source, query, mined=mined)

    # --- kanji lookup mode ------------------------------------------------------------------------
    def kanji_current(self) -> None:
        nested_popup.kanji_current(self)

    def _open_kanji(self, ch: str, wx: float, wy: float, wh: float) -> None:
        nested_popup.open_kanji(self, ch, wx, wy, wh)

    def _click_kanji_fallback(self, x: float, y: float) -> None:
        nested_popup.click_kanji_fallback(self, x, y)

    def _hide_nested(self) -> None:
        nested_popup.hide_nested(self)

    # --- mining (flow lives in app/miner.py; thin delegates here) --------------------------------
    def _mine_target(self) -> int | None:
        return self._miner.mine_target()

    def _sentence_html(self) -> str:
        return "<br>".join("".join(t.surface for t in line) for line in self.lines)

    _tag_slug = staticmethod(tag_slug)

    def _source_meta(self, video):
        from saitenka.app.miner import source_meta

        return source_meta(video)

    def _provenance(self, video) -> str:
        return self._miner.provenance(video)

    def _mine_tags(self, video) -> list[str]:
        return self._miner.mine_tags(video)

    def mine_current(self, *, animated: bool | None = None) -> None:
        if not self.anki or not self.mine_cfg:
            log.info(
                "mine ignored: anki=%s mine_cfg=%s", self.anki is not None, bool(self.mine_cfg)
            )
            return
        idx = self._mine_target()
        if idx is None:
            self._toast("no word to mine", "warn")
            log.info("mine: no target word (animated=%s)", bool(animated))
            return
        # Log the KEY-driven mine (still vs video) — without this, the trace can't tell a Ctrl+Shift+m
        # video-mine from a plain one, and a keypress that reached the handler from one that never did.
        log.info("mine: %r animated=%s", self.tokens[idx].surface, bool(animated))
        with otel_metrics.traced("anki_mine", source="base") as span:
            span.set("animated", bool(animated))
            self._miner.mine_token(self.tokens[idx], animated=animated)

    def mine_current_video(self) -> None:
        """The video-mine shortcut: mine the hovered word with an animated (motion) screenshot, even when
        ``[mine].animated_screenshot`` is off."""
        self.mine_current(animated=True)

    def _mine_token(self, tok, *, card=None) -> None:
        with otel_metrics.traced("anki_mine", source="nested"):
            self._miner.mine_token(tok, card=card)

    def _mark_mined(self, expression: str) -> None:
        miner_ui.mark_mined(self, expression)

    def _rerender_nested(self) -> None:
        miner_ui.rerender_nested(self)

    # --- card preview (verify correctness / image / sound, one surface) — logic in app/miner_ui.py
    def _sentence_lines(self) -> list[str]:
        return miner_ui.sentence_lines(self)

    def _footer(self, video) -> str:
        return miner_ui.footer(self, video)

    def _preview_mined(self, card, tok, video, status: str = "mined") -> None:
        if not self.show_preview:
            self._toast(f"mined {card.expression}")  # preview off → a terse confirmation instead
            return
        miner_ui.preview_mined(self, card, tok, video, status)

    def _add_duplicate(self) -> None:
        """The preview's ＋ button: mine a second card for the current scene even though the
        expression is already in the deck (a different line/episode/anime)."""
        if self.preview.dup_tok is not None:
            self._miner.mine_token(self.preview.dup_tok, force=True)

    def _preview_existing(self, note_id: int, card, status: str) -> None:
        if not self.show_preview:
            self._toast(f"already have {card.expression}")
            return
        miner_ui.preview_existing(self, note_id, card, status)

    def _media_image(self, name):
        return miner_ui.media_image(self, name)

    def _media_tempfile(self, name):
        return miner_ui.media_tempfile(self, name)

    def _show_preview(self, pv: PreviewData, audio_path) -> None:
        miner_ui.show_preview(self, pv, audio_path)

    def _render_preview(self) -> None:
        miner_ui.render_preview(self)

    def _hide_preview(self) -> None:
        miner_ui.hide_preview(self)

    def _click_preview(self, x: float, y: float) -> bool:
        return miner_ui.click_preview(self, x, y)

    def replay_preview(self) -> None:
        miner_ui.replay_preview(self)

    def _frequency(self, tok) -> tuple[str, str]:
        return self._miner.frequency(tok)

    def _capture_media(self, base: str, video) -> tuple[str, str]:
        return self._miner.capture_media(base, video)

    def bulk_mine(self) -> None:
        self._miner.bulk_mine()

    # --- translation reveal (EN secondary track) ----------------------------------------------
    def _setup_secondary(self) -> int | None:
        return translation.setup_secondary(self)

    def _translation_visible(self) -> bool:
        return translation.translation_visible(self)

    def _sync_auto_translation(self) -> None:
        translation.sync_auto_translation(self)

    def toggle_translation(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.TOGGLE_TRANSLATION)

    def toggle_overlay(self) -> None:
        if self.ov.visible:
            self.hover = -1
            self._teardown_tip()
            self.ov.set_visible(visible=False)
            subtitle_modes.release_secondary(self)
            self.subtitle_pipeline.suspend_for_overlay(self)
            return
        self.ov.set_visible(visible=True)
        self.subtitle_pipeline.resume_after_overlay(self)
        if self._translation_visible():
            self._setup_secondary()
            self._draw_translation()

    def configure_subtitle_mode(
        self, startup: subtitle_modes.SubtitleStartup, *, slang: str = "ja,jpn,jp"
    ) -> None:
        subtitle_modes.configure(self, startup, slang=slang)

    # --- subtitle-owned commands: pure reducer, executed here (WP4.2) -------------------------
    def _subtitle_inputs(self) -> subtitle_intents.SubtitleInputs:
        """Read every fact the subtitle commands decide from, once, before deciding."""
        from saitenka.app.subtitle_modes import _current_external_sub

        index = self._sub_index
        playhead = self._prop("time-pos")
        return subtitle_intents.SubtitleInputs(
            tracks=subtitle_modes.discover_tracks(self.ipc, self.subtitle_slang),
            active_sid=self._get("sid"),
            language=self.subtitle_language,
            annotation_mode=self.annotation_mode,
            has_cue=bool(self.sub_text.strip()),
            retry_in_flight=self.episode.subtitle.retry_active,
            media_path=self._get("path"),
            has_external_sub=_current_external_sub(self.ipc) is not None,
            has_cue_lines=bool(self.lines),
            cue_starts=tuple(cue.start for cue in index.cues) if index is not None else (),
            playhead=None if playhead is None else float(playhead),
            sub_delay=float(self._prop("sub-delay") or 0.0),
            cue_revision=self.cue_revision,
        )

    def _run_subtitle_command(self, command: subtitle_intents.SubtitleCommand) -> None:
        for effect in subtitle_intents.reduce(command, self._subtitle_inputs()):
            self._apply_subtitle_effect(effect)

    def _apply_subtitle_effect(self, effect: subtitle_intents.SubtitleEffect) -> None:
        if isinstance(effect, subtitle_intents.SelectTrack):
            subtitle_modes.select_track(self, effect.sid, effect.target)
        elif isinstance(effect, subtitle_intents.AdoptCurrentAsTarget):
            subtitle_modes.adopt_current_as_target(self, effect.sid)
        elif isinstance(effect, subtitle_intents.AcquireSubtitles):
            subtitle_modes.begin_acquisition(self, effect.media_path, effect.source)
        elif isinstance(effect, subtitle_intents.SetAnnotationMode):
            self.annotation_mode = effect.mode
            self._annotation_hover = False
            if effect.redraw:
                self._draw_subtitle()
        elif isinstance(effect, subtitle_intents.SeekCue):
            self._seek_cue(effect)
        elif isinstance(effect, subtitle_intents.SetSubtitleDelay):
            self.ipc.command("set_property", "sub-delay", f"{effect.seconds:.3f}")
        elif isinstance(effect, subtitle_intents.CopyCueText):
            copy_clipboard("\n".join(self._sentence_lines()))
        elif isinstance(effect, subtitle_intents.ToggleTranslation):
            translation.toggle_translation(self)
        elif isinstance(effect, subtitle_intents.Announce):
            self._toast(effect.text, effect.kind)

    def toggle_subtitle_language(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.TOGGLE_LANGUAGE)

    def mark_current_subtitle_japanese(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.MARK_CURRENT_JAPANESE)

    def fetch_japanese_subs_async(self, fetch) -> None:
        subtitle_modes.start_fetch(self, fetch, select_if_unchanged=True)

    def configure_subtitle_retry(self, factory) -> None:
        subtitle_modes.configure_retry(self, factory)

    def retry_japanese_subtitles(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.RETRY_ACQUISITION)

    def _secondary_text(self) -> str:
        return translation.secondary_text(self)

    def _draw_translation(self) -> None:
        translation.draw_translation(self)

    def _toast(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        img = render_toast(text, kind)
        x = (self.osd[0] - img.width) // 2
        y = round(self.osd[1] * 0.08)
        self.lifecycle_surfaces.present(img, x, y, oid=TOAST_ID)
        from saitenka.app.lifecycle_timers import LifecycleTimerKind

        scheduled = self.lifecycle_timers.schedule(
            LifecycleTimerKind.TOAST_EXPIRY,
            seconds,
            lambda: self.lifecycle_surfaces.remove(TOAST_ID),
        )
        if not scheduled:
            self.lifecycle_surfaces.remove(TOAST_ID)

    def toggle_hover_pause(self) -> None:
        self.pause_on_tooltip = not self.pause_on_tooltip
        if not self.pause_on_tooltip and self._paused_by_tip:
            self.ipc.command("set_property", "pause", False)  # noqa: FBT003  # mpv IPC wire value
            self._paused_by_tip = False
        state = "on" if self.pause_on_tooltip else "off"
        self._toast(f"hover auto-pause: {state}")

    def toggle_bookmark(self) -> None:
        backlog.capture_current(self)

    def toggle_sidebar(self) -> None:
        sidebar.toggle(self)

    def toggle_sub_picker(self) -> None:
        sub_picker.toggle(self)

    def configure_sub_picker(self, lister: Callable[[str], tuple]) -> None:
        sub_picker.configure(self, lister)

    def toggle_analysis(self) -> None:
        analysis_overlay.toggle(self)

    def toggle_annotation_mode(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.TOGGLE_ANNOTATION_MODE)

    def toggle_help(self) -> None:
        help_overlay.toggle(self)

    def _register_keybinds(self) -> None:
        # mpv `keybind` takes the command as ONE string, e.g. "script-message saitenka-speak".
        # CRITICAL: passing the command as split args silently kills the key — always one string.
        def bind(key: str, msg: str) -> None:
            reply = self.ipc.command("keybind", key, f"script-message {msg}")
            # Surface a REJECTED binding (bad key name, or mpv refusing it) — silently dropping mpv's
            # reply is why a non-firing shortcut (e.g. attach-mode Ctrl+Shift+m) was undiagnosable. In
            # attach/plugin mode another script or the user's input.conf can also shadow the key; the
            # registration line is the ground truth for "what did we actually bind".
            err = reply.get("error") if isinstance(reply, dict) else None
            if err and err != "success":
                log.warning("keybind %r -> %s rejected by mpv: %s", key, msg, err)
            else:
                log.debug("keybind %r -> script-message %s", key, msg)

        # active_bindings no longer gates on `requires` — bind the anki/tts actions even when the dep
        # isn't up YET (attach mode loads Anki async, after this runs, and we never re-register). The
        # handlers (mine_current/bulk_mine/speak) no-op with a toast when the dep is absent.
        bound = 0
        for binding in active_bindings(self, "global"):
            message = binding.spec.message
            if message is not None:
                bind(binding.key, message)
                bound += 1
        log.info("registered %d global keybinds (anki=%s)", bound, self.anki is not None)
        self._define_mouse_section()  # "mouse"-scoped controls live in a forced section, enabled on demand

    def _navigate_previous(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.NAVIGATE_PREVIOUS)

    def _navigate_next(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.NAVIGATE_NEXT)

    def _replay_cue(self) -> None:
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.REPLAY_CUE)

    def _anchor_subtitles(self) -> None:
        """One-press manual re-time: snap the sub cue nearest the playhead to start NOW. For when
        auto-sync leaves a residual offset (e.g. a different-length OP), pause as a line's audio
        begins and press — mpv's ``sub-delay`` shifts so that cue lands here, and every later cue
        follows by the same offset. The overlay reads the delayed ``sub-text``, so the on-screen line
        moves with it. Cumulative (anchors from the current delay), so a second anchor refines a first."""
        self._run_subtitle_command(subtitle_intents.SubtitleCommand.ANCHOR_TIMING)

    def _build_command_router(self) -> LegacyCommandExecutor:
        """Assemble feature-owned actions once; handlers are bound and receive no god context."""

        def action(method_name: str) -> Callable[[], None]:
            return lambda: getattr(self, method_name)()

        def seek(delta: int) -> None:
            self.subtitle_pipeline.invalidate()
            self._sub_nav(delta)
            self.ipc.command("sub-seek", str(delta))

        def scroll(delta: int) -> Callable[[], None]:
            return lambda: self._scroll_tip(round(self._tip_ref_h * 0.12) * delta)

        def wheel(steps: int) -> Callable[[], None]:
            def route() -> None:
                surfaces.route_scroll(self, steps)

            return route

        handlers = {
            MINE_MSG: action("mine_current"),
            MINE_VIDEO_MSG: action("mine_current_video"),
            MINE_ALL_MSG: action("bulk_mine"),
            OVERLAY_TOGGLE_MSG: action("toggle_overlay"),
            PROFILE_CYCLE_MSG: action("cycle_profile"),
            HOVER_PAUSE_MSG: action("toggle_hover_pause"),
            BOOKMARK_MSG: action("toggle_bookmark"),
            SIDEBAR_MSG: action("toggle_sidebar"),
            SUB_PICKER_MSG: action("toggle_sub_picker"),
            ANALYSIS_MSG: action("toggle_analysis"),
            HELP_TOGGLE_MSG: action("toggle_help"),
            HELP_PREV_MSG: lambda: help_overlay.step(self, -1),
            HELP_NEXT_MSG: lambda: help_overlay.step(self, 1),
            HELP_CLOSE_MSG: lambda: help_overlay.close_help(self),
            PREVIEW_MSG: action("replay_preview"),
            PREVIEW_CLOSE_MSG: action("_hide_preview"),
            SCROLL_UP_MSG: wheel(-1),
            SCROLL_DOWN_MSG: wheel(1),
            SPEAK_MSG: action("speak_hovered"),
            COPY_MSG: action("copy_hovered"),
            COPY_CLICK_MSG: action("copy_click"),
            CLICK_MSG: action("on_click"),
            KANJI_MSG: action("kanji_current"),
            TIP_UP_MSG: scroll(-1),
            TIP_DOWN_MSG: scroll(1),
            TIP_CLOSE_MSG: action("_tip_close_or_back"),
        }
        # Migrated (WP4.2 / WP4.5): the decision is `subtitle_intents.reduce`, so these carry
        # no compatibility binding at all.
        reducers = {
            SUBTITLE_LANGUAGE_MSG: action("toggle_subtitle_language"),
            SUBTITLE_MARK_JP_MSG: action("mark_current_subtitle_japanese"),
            SUBTITLE_RETRY_MSG: action("retry_japanese_subtitles"),
            ANNOTATION_MSG: action("toggle_annotation_mode"),
            TRANS_MSG: action("toggle_translation"),
            COPY_LINE_MSG: action("copy_line"),
            SUB_PREV_MSG: action("_navigate_previous"),
            SUB_NEXT_MSG: action("_navigate_next"),
            SUB_REPLAY_MSG: action("_replay_cue"),
            SUB_ANCHOR_MSG: action("_anchor_subtitles"),
        }
        return LegacyCommandExecutor(
            {
                name: LegacyCommandBinding(handler, "work-package-5")
                for name, handler in handlers.items()
            },
            reducers=reducers,
        )

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
            help_open=self._help_open,
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

    def _build_tick_pipeline(self) -> TickPipeline:
        return TickPipeline(
            (
                TickStage("refresh-osd", self._refresh_surfaces),
                TickStage("apply-background-results", self._apply_background_results),
                TickStage("update-interaction", self._update_interaction),
            )
        )

    def _refresh_surfaces(self) -> None:
        if self.refresh_osd():
            if self.sub_text.strip():
                self._draw_subtitle()
            help_overlay.redraw(self)
            analysis_overlay.redraw(self)

    def _apply_background_results(self) -> None:
        self._apply_pending_deps_or_spinner()
        self._apply_capabilities()
        tooltip.apply_pending_crisp(self, self._tip_view)
        tooltip.apply_pending_crisp(self, self._nest)
        sidebar.update(self)

    def _apply_capabilities(self) -> None:
        if self._tts_capability is not None:
            if self._tts_capability.value is not None:
                self._tts_ok = bool(self._tts_capability.value)
            self._tts_capability.request()
        if self._anki_capability is not None:
            if self._anki_capability.value:
                self._request_mined_seed()
            self._anki_capability.request()

    def _request_mined_seed(self) -> None:
        if (
            self._mined_seed_inflight
            or self._mined_seed_done
            or time.monotonic() < self._mined_seed_next_due
            or self.anki is None
            or self.mine_cfg is None
        ):
            return
        if self._mined_seed_submit is None:
            return
        self._mined_seed_inflight = True
        generation = self._mined_seed_generation
        self._mined_seed_submit(
            owner=Owner.SESSION,
            identity=generation,
            lane="mined-seed",
            request=mined_seed.MinedSeedRequest(self.anki, self.mine_cfg),
            on_finished=self._finish_mined_seed,
        )

    def _finish_mined_seed(self, completion: EffectFinished) -> None:
        generation = completion.identity
        if (
            not isinstance(generation, int)
            or generation != self._mined_seed_generation
            or self._stop.is_set()
        ):
            return
        self._mined_seed_inflight = False
        values = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
        if not isinstance(values, set):
            self._mined_seed_failures += 1
            self._mined_seed_next_due = time.monotonic() + min(
                8.0, 0.25 * (2 ** (self._mined_seed_failures - 1))
            )
            return
        self._mined_seed_done = True
        self._mined_seed_failures = 0
        before = len(self._mined)
        self._mined.update(values)
        self._mined_generation += int(len(self._mined) != before)

    def _finish_analysis(self, completion: EffectFinished) -> None:
        changed = analysis_overlay.finish(self.analysis, completion)
        if completion.outcome is not EffectOutcome.REJECTED:
            changed |= analysis_overlay.submit_pending(
                self.analysis, self._analysis_submit, self._finish_analysis
            )
        if changed:
            analysis_overlay.redraw(self)

    def _request_interaction_metadata(self, request: hover_metadata.MetadataRequest) -> bool:
        return hover_metadata.submit(
            self._interaction_metadata,
            request,
            self._interaction_metadata_submit,
            self._finish_interaction_metadata,
        )

    def _finish_interaction_metadata(self, completion: EffectFinished) -> None:
        result = hover_metadata.finish(self._interaction_metadata, completion)
        try:
            if isinstance(result, hover_metadata.HoverMetadata):
                tooltip.apply_hover_metadata(self, result)
            elif isinstance(result, hover_metadata.NestedMetadata):
                nested_popup.apply_nested_metadata(self, result)
        finally:
            hover_metadata.finish_publication(self._interaction_metadata)
            hover_metadata.submit_pending(
                self._interaction_metadata,
                self._interaction_metadata_submit,
                self._finish_interaction_metadata,
            )

    def _request_render_ahead(self, view: PopupView, direction: int) -> bool:
        panel = view.state
        if panel is None:
            return False
        return tooltip_raster.request(
            self._render_ahead,
            tooltip_raster.RenderAheadRequest(
                panel,
                view.desired_scroll,
                view.view_h,
                direction,
                self._raster_scale,
                threading.Event(),
            ),
            generation=self._prefetch_gen,
            job_id=view.job_id,
            submit=self._render_ahead_submit,
            on_finished=self._finish_render_ahead,
        )

    def _request_engaged_tooltip(self, request: tooltip_engaged.EngagedRequest) -> bool:
        return tooltip_engaged.submit(
            self._engaged_tooltip,
            request,
            generation=self._prefetch_gen,
            submitter=self._engaged_tooltip_submit,
            on_finished=self._finish_engaged_tooltip,
        )

    def _finish_engaged_tooltip(self, completion: EffectFinished) -> None:
        finished = tooltip_engaged.finish(
            self._engaged_tooltip,
            completion,
            self._engaged_tooltip_submit,
            self._finish_engaged_tooltip,
        )
        if finished is None:
            return
        identity, request, result, succeeded, superseded, rejected = finished
        if rejected is not None:
            rejected_identity, rejected_request = rejected
            if rejected_identity.generation == self._prefetch_gen:
                self._fallback_engaged_tooltip(rejected_request)
        if identity.generation != self._prefetch_gen:
            return
        if superseded:
            return
        if isinstance(request, tooltip_engaged.HoverRequest) and not succeeded:
            self._interaction_jobs.finish("tooltip", "failed", job_id=request.job_id)
            return
        if not succeeded:
            self._fallback_engaged_tooltip(request)
            return
        if isinstance(result, tooltip_engaged.HoverReady):
            tooltip.apply_engaged_hover(self, result)
        elif isinstance(result, tooltip_engaged.NavigateReady):
            tooltip.apply_engaged_nav(self, result)
        elif isinstance(result, tooltip_engaged.OpenReady):
            tooltip.apply_engaged_open(self, result)

    def _fallback_engaged_tooltip(self, request: tooltip_engaged.EngagedRequest) -> None:
        if isinstance(request, tooltip_engaged.NavigateRequest | tooltip_engaged.OpenRequest) and (
            request.origin != id(self._tip_state)
        ):
            return
        try:
            result = tooltip_engaged.run_engaged(
                tooltip_engaged.EngagedWork(request, threading.Event()),
                threading.Event(),
                self._engaged_tooltip_backend,
            )
        except Exception:
            log.warning("engaged tooltip fallback failed", exc_info=True)
            return
        if isinstance(result, tooltip_engaged.HoverReady):
            tooltip.apply_engaged_hover(self, result)
        elif isinstance(result, tooltip_engaged.NavigateReady):
            tooltip.apply_engaged_nav(self, result)
        elif isinstance(result, tooltip_engaged.OpenReady):
            tooltip.apply_engaged_open(self, result)

    def _cancel_engaged_tooltip(self) -> None:
        tooltip_engaged.cancel(self._engaged_tooltip)

    def _finish_render_ahead(self, completion: EffectFinished) -> None:
        finished = tooltip_raster.finish(
            self._render_ahead,
            completion,
            self._render_ahead_submit,
            self._finish_render_ahead,
        )
        if finished is None:
            return
        identity, request, succeeded = finished
        if identity.generation != self._prefetch_gen:
            return
        for view in (self._tip_view, self._nest):
            if (
                view.state is request.panel
                and view.desired_scroll == identity.scroll
                and view.job_id == identity.job_id
            ):
                if succeeded:
                    tooltip.apply_pending_scroll(self, view)
                    tooltip.apply_pending_crisp(self, view)
                else:
                    view.desired_scroll = view.scroll
                    self._interaction_jobs.finish("scroll", "failed", job_id=identity.job_id)
                return

    def _cancel_render_ahead(self) -> None:
        tooltip_raster.cancel(self._render_ahead)

    def _submit_subtitle_fetch(
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
                    self, subtitle_modes.finish_fetch(request, completion)
                )
            finally:
                if on_done is not None:
                    on_done()

        submitter = self._subtitle_fetch_submit
        if submitter is None:
            subtitle_modes.apply_fetch_result(self, subtitle_modes.unavailable_fetch(request))
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
        sub_picker.close_picker(self)
        self._subtitle_force_select_revision += 1
        self.episode = EpisodeContext()

    def _update_interaction(self) -> None:
        self._feed_episode_annotation()
        self._update_hover()
        self._sync_mouse_capture()
        self._update_prefetch()
        if self._translation_visible() and self._secondary_text() != self._trans_text:
            self._draw_translation()

    # --- run loop -----------------------------------------------------------------------------
    def _maybe_advance(self) -> None:
        """On the eof-reached rising edge, ask the installed hook to re-slot to the next episode (#100).

        One-shot per file: `_eof_handled` blocks a repeat call while mpv sits paused at EOF, and re-arms
        once a fresh file clears eof-reached. A hook that returns False (SyncPlay/attach never installs
        one, no sibling, ambiguous) is a no-op — mpv just holds the last frame until the user quits."""
        if self.advance_hook is None:
            return
        if self._prop("eof-reached"):
            if not self._eof_handled:
                self._eof_handled = True
                self.advance_hook()
        else:
            self._eof_handled = False

    def current_media_path(self) -> Path | None:
        """mpv's current file as an absolute path (``path`` is verbatim what was loaded, so resolve a
        relative one against ``working-directory``). None when nothing is loaded. Used by the reactive
        re-slot and the eof advance to key the #100 sibling resolver off the real filesystem path."""
        raw = self._prop("path")
        if not raw:
            return None
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            wd = self._prop("working-directory")
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

    def poll_once(self) -> bool:
        """One tick: sync subtitle + hover, handle key events. False if mpv went away."""
        try:
            self._scrolled_this_tick = False  # set by _scroll_tip below (wheel or TIP_UP/DOWN)
            # Sampled before the drain: cue reconciliation draws from the batch boundary, so a
            # sample taken after it would miss the very draw the paused nudge exists to re-flush.
            ops_before = self.ov.ops
            self._drain_events()
            if not self._connection_ready:
                return True
            session_stats.tick(self)
            self._maybe_advance()
            self._flush_paused_nudge()
            first_tick = not self._interactive_ready
            if first_tick:
                with otel_metrics.traced("startup.first_tick"):
                    self.tick_pipeline.run(traced_prefix="startup.first_tick")
            else:
                self.tick_pipeline.run()
            self._schedule_paused_nudge(ops_before)
            self._mark_interactive_ready()
            return True
        except (OSError, ValueError):
            return False

    def _flush_paused_nudge(self) -> None:
        """A draw landed while paused last tick — poke the throttled OSD so mpv actually presents it."""
        if not self._nudge_pending:
            return
        self._nudge_pending = False
        self.ov.repaint()
        # No per-nudge log line — the osd_paused_nudge counter below carries the count (this fired
        # ~1600×/session at debug, 67% of the log, duplicating the counter for zero added detail).
        if otel_metrics.osd_paused_nudge is not None:
            otel_metrics.osd_paused_nudge.add(1)

    def _drain_events(self) -> None:
        picker_guard = LegacyPickerRepeatGuard()
        # This drain owns a whole turn, so completions come back in envelope sequence and are
        # dispatched below in order with the observations they followed.
        for ev in self.ipc.drain_events(ordered_terminals=True):
            self._drain_event(ev, picker_guard)
        self._settle_cue_observation()

    def _settle_cue_observation(self) -> None:
        """Reconcile at the batch boundary, not per delta. mpv splits one cue across sub-text,
        sub-start and sub-end observations; the projection publishes each (it sees one observation
        at a time and has no batch), so reconciling per delta would build the cue three times, twice
        against a half-updated identity. The drain is where the batch exists, so it coalesces."""
        cue, self._pending_cue = self._pending_cue, None
        # A reconcile that decides to do nothing used to be as silent as a geometry schedule that
        # never started; the two together are what make a dropped cue traceable.
        with otel_metrics.traced("cue_reconcile") as span:
            span.set("cue_revision", self.cue_revision)
            if cue is None:
                span.set("outcome", "no-observation")
                return
            before = self.sub_text
            self._reconcile_sub_text(cue.text)
            span.set("outcome", "adopted" if self.sub_text != before else "reinstalled")

    @property
    def cue_revision(self) -> int:
        """The projection's cue revision — the identity a geometry refresh was armed for."""
        return self._playback.cue.cue.value

    def _drain_event(self, ev: object, picker_guard: LegacyPickerRepeatGuard) -> None:
        if isinstance(ev, EffectFinished):
            self.ipc.dispatch_runtime_terminal(ev)
            return
        if isinstance(ev, ConnectionLost):
            self._connection_ready = False
            self._retire_cue_identity("connection-lost")
            return
        if isinstance(ev, ConnectionReplaced):
            self._on_ipc_reconnect()
            return
        if isinstance(ev, ConnectionReady):
            self._connection_ready = True
            return
        if isinstance(ev, UserCommand):
            if not self._connection_ready:
                self._publish_command_event(
                    CommandHandled(
                        ev.name,
                        None,
                        CommandOutcome.REJECTED,
                        command_id=ev.command_id,
                        reason=CommandReason.DISCONNECTED,
                    )
                )
                return
            self._drain_command(ev, picker_guard)
            return
        if not isinstance(ev, dict):
            log.debug("ignored unsupported runtime event: %s", type(ev).__name__)
            return
        kind = ev.get("event")
        if kind == "file-loaded":
            picker_guard.separate()
            self._on_file_loaded()
        elif kind == "property-change":
            self._on_property_change(ev)
        elif kind == "client-message":
            args = ev.get("args") or [""]
            name = args[0] if isinstance(args[0], str) else ""
            self._drain_command(UserCommand(name, tuple(args[1:])), picker_guard)

    def _drain_command(self, command: UserCommand, guard: LegacyPickerRepeatGuard) -> None:
        if (suppressed := guard.inspect(command)) is not None:
            log.debug("script-message: %s (coalesced in current IPC batch)", command.name)
            self._publish_command_outcome(suppressed)
            return
        self._handle(command)

    def _publish_command_outcome(self, result: CommandExecution) -> None:
        self._publish_command_event(result.event())

    def _publish_command_event(self, event: CommandHandled) -> None:
        publish = getattr(self.ipc, "publish_legacy_command_outcome", None)
        if publish is not None:
            publish(event)

    def arm_hover_deadline(self, kind, seconds: float, due) -> bool:
        """Arm one hover dwell deadline, superseding any earlier one of the same kind."""
        return self.lifecycle_timers.schedule(kind, seconds, due)

    def cancel_hover_deadline(self, kind) -> None:
        """Retire a dwell the cursor has moved off. The revision bump is the point — a due event
        already in flight has to be fenced, not merely unscheduled."""
        self.lifecycle_timers.cancel(kind)

    def hold_sidebar_scroll(self, seconds: float) -> bool:
        """Arm the deadline that releases the sidebar's manual-scroll hold, resuming auto-follow.

        The due event runs `sidebar.update` rather than only clearing the flag: a hold that expires
        while the cue has not moved would otherwise leave the sidebar off-target until the next cue
        happened to arrive.
        """
        from saitenka.app.lifecycle_timers import LifecycleTimerKind

        def released() -> None:
            self.sidebar.manual_hold = False
            sidebar.update(self)

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.SIDEBAR_MANUAL_HOLD, seconds, released
        )

    def schedule_flash_expiry(self) -> bool:
        """Arm the deadline that ends a copy-flash pulse. A second flash supersedes the first,
        which `LifecycleTimers` fences by revision so only the latest due event lands."""
        from saitenka.app.lifecycle_timers import LifecycleTimerKind

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.FLASH_EXPIRY, self.flash_secs, self._flash_expired
        )

    def _flash_expired(self) -> None:
        oid, self._flash_oid = self._flash_oid, None
        if oid == NESTED_ID:
            self._render_nested_view()  # redraw without the highlight border
        elif oid == TIP_ID:
            self._render_tip_view()

    def _mark_interactive_ready(self) -> None:
        if self._interactive_ready:
            return
        if self._observing and self._playback.value("osd-dimensions") in (None, {}):
            return
        self._interactive_ready = True
        connected_at = getattr(self.ipc, "connected_at", None)
        with otel_metrics.traced(
            "startup.interactive_ready",
            cue_pending=str(self._sub_pending is not None).lower(),
            deps_pending=str(not self._dependencies_settled).lower(),
            hint_owned=str(self._startup_hint_lease is not None).lower(),
        ) as span:
            if connected_at is not None:
                span.set("since_ipc_ms", round((time.monotonic() - connected_at) * 1_000, 3))
            if self._startup_hint_lease is not None:
                self._startup_hint_lease.mark_ready()

    def _apply_pending_deps_or_spinner(self) -> None:
        """Progressive startup: inject background-loaded deps (once), else animate the spinner."""
        if self._pending_deps is not None:
            deps, self._pending_deps = self._pending_deps, None
            self._apply_deps(deps)

    def _schedule_paused_nudge(self, ops_before: int) -> None:
        """An overlay changed while mpv is paused → schedule a re-flush next tick so mpv actually
        presents it (mpv #8172; see Overlay.repaint). Only when paused: playing frames present on
        their own, and re-adding every tick would be wasteful."""
        if self.ov.ops != ops_before and self._prop("pause"):
            self._nudge_pending = True
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
        bytes_read = getattr(self.ipc, "_bytes_read", -1)
        osd_ok = self._prop("osd-dimensions") not in (None, {})
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

    def _seed_mined(self) -> None:
        before = len(self._mined)
        self._miner.seed_mined()
        self._mined_generation += int(len(self._mined) != before)

    # --- subtitle navigation (instant render, then seek) --------------------------------------
    def load_sub_index(self, path) -> None:
        subnav.load_sub_index(self, path)

    def _seek_cue(self, effect: subtitle_intents.SeekCue) -> bool:
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
        self.ipc.command("sub-seek", str(effect.delta))
        return True

    def _sub_nav(self, delta: int) -> bool:
        return subnav.sub_nav(self, delta)

    def _reconcile_sub_text(self, text: str) -> None:
        subnav.reconcile_sub_text(self, text)

    # --- progressive dep loading --------------------------------------------------------------
    def load_deps_async(self, cfg: dict, build=None, *, prebuilt=None) -> None:
        reader_deps.load_deps_async(self, cfg, build, prebuilt=prebuilt)

    def _apply_deps(self, deps: dict) -> None:
        reader_deps.apply_deps(self, deps)

    def _draw_loading(self) -> None:
        reader_deps.draw_loading(self)

    def _schedule_loading_frame(self, *, delay_s: float) -> bool:
        from saitenka.app.lifecycle_timers import LifecycleTimerKind

        return self.lifecycle_timers.schedule(
            LifecycleTimerKind.LOADING_FRAME,
            delay_s,
            self._loading_frame_due,
        )

    def _loading_frame_due(self) -> None:
        if not self._loading or self._pending_deps is not None:
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
        print(  # noqa: T201  # user-facing banner; log.info alone won't show — console handler is WARNING-level (logsetup.py)
            f"[saitenka] runtime: {mode} · {self.prefetch_state.workers} prefetch worker(s)"
        )
        log.info("runtime: %s, %d prefetch worker(s)", mode, self.prefetch_state.workers)

    def run(self, interval: float | None = None) -> None:
        from saitenka.render.banded import guard_main_render
        from saitenka.version import overlay_version

        # First line of every session's log: pins WHICH build actually drew (both run + attach reach
        # here). `doctor` reads it back to catch a stale attach process — an mpv left open across an
        # editable reinstall keeps its old modules until relaunched (see doctor.check_stale_overlay).
        log.info("saitenka overlay %s starting", overlay_version())
        guard_main_render(
            on=True
        )  # this IS the render loop — native rasterisation must run on a worker
        interval = interval if interval is not None else self.poll_interval
        with otel_metrics.traced("startup.reader_setup"):
            self.refresh_osd()
            with otel_metrics.traced("startup.reader_setup.observers"):
                self.start_observing()  # event-driven property reads from here on
            with otel_metrics.traced("startup.reader_setup.keybinds"):
                self._register_keybinds()
            self._seed_mined()
            session_stats.start(self)
            telemetry.set_gauge_provider(
                self._telemetry_gauges
            )  # no-op unless telemetry is configured
        # In run/attach the deps (and thus the prefetch lane) load ASYNC — dict_set is still None here,
        # so construction-time start_prefetch was a no-op and the worker count is 0. Defer the banner to when
        # prefetch actually starts (apply_deps → _announce_runtime); only announce now on the sync path
        # (deps already present, e.g. a demo/screenshot run) where apply_deps is never called.
        if self.dict_set is not None:
            self._announce_runtime()
        from saitenka.app.lifecycle_timers import LifecycleTimerKind

        self.lifecycle_timers.schedule(
            LifecycleTimerKind.STARTUP_HEALTH,
            8.0,
            self._check_startup_health,
        )
        while self.poll_once():
            time.sleep(interval)

    def _on_ipc_reconnect(self) -> None:
        self.subtitle_pipeline.connection_replaced(self)
        if self._startup_hint_lease is not None:
            self._startup_hint_lease.connection_replaced()

    def close(self) -> None:
        import shutil

        if self._tts_capability is not None:
            self._tts_capability.close()
        if self._anki_capability is not None:
            self._anki_capability.close()
        self._mined_seed_generation += 1
        self._interaction_jobs.cancel_all()
        hover_metadata.close(self._interaction_metadata)
        self._release_mouse_capture()  # hand the mouse back before a detached mpv outlives us
        telemetry.set_gauge_provider(None)  # drop our cache-gauge closure before teardown
        self._stop.set()  # signal the workers; they do no IPC so this is race-free
        close_lane = getattr(self.ipc, "close_runtime_job_lane", None)
        deadline = time.monotonic() + 2.0
        # Each `close_lane` call site is duty evidence the migration checker matches by name and
        # order, so these stay literal — routing them through one helper reads as a lost close.
        if close_lane is not None:
            close_lane("subtitle-fetch", max(0.0, deadline - time.monotonic()))
            close_lane("subtitle-picker", max(0.0, deadline - time.monotonic()))
            # Stop the executor before the state it renders against is torn down: a job admitted
            # after this cannot outlive `native_geometry.close()` below.
            close_lane(GEOMETRY_LANE, max(0.0, deadline - time.monotonic()))
        if self._annotation is not None:
            self._annotation.close()
        if close_lane is not None:
            close_lane("cue-annotation", max(0.0, deadline - time.monotonic()))
        tooltip_raster.close(self._render_ahead)
        if close_lane is not None:
            close_lane("tooltip-render-ahead", max(0.0, deadline - time.monotonic()))
        tooltip_engaged.close(self._engaged_tooltip)
        if close_lane is not None:
            close_lane("tooltip-engaged", max(0.0, deadline - time.monotonic()))
        prefetch.close(self.prefetch_state)
        if close_lane is not None:
            close_lane("speculative-prefetch", max(0.0, deadline - time.monotonic()))
        mask_atlas_startup.close(self._mask_atlas_startup)
        if close_lane is not None:
            close_lane("mask-atlas-startup", max(0.0, deadline - time.monotonic()))
        mask_atlas_startup.uninstall(self.session.render_cache)
        self.retire_geometry_refresh()  # no refresh may land after the provider closes
        self.retire_settle_window()  # nor may a settle deadline outlive the session
        if self.native_geometry is not None:
            self.subtitle_pipeline.deactivate(self)
            self.subtitle_pipeline.clear(self)
            self.native_geometry.close()
        else:
            self.subtitle_pipeline.deactivate(self)
            self.subtitle_pipeline.close()
        stats_summary = session_stats.finish(self)
        if stats_summary and self.options.stats.summary:
            print(f"[saitenka] session: {stats_summary}")  # noqa: T201  # requested close summary
        if self._backlog_store is not None:
            self._backlog_store.close()
        if self._mined_store is not None:
            self._mined_store.close()
        self.lifecycle_timers.close()
        self.lifecycle_surfaces.close()
        self.ov.close()
        shutil.rmtree(self._tmp, ignore_errors=True)  # clean up the per-session scratch dir
