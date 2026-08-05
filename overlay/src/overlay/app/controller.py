"""The MVP reader loop: mpv subtitle → my overlay → hover → dictionary tooltip.

Polls mpv over IPC (no Lua): reads ``sub-text`` (native subs hidden) and ``mouse-pos``, draws the
subtitle as overlay #1 with per-word hitboxes, and on hover draws the looked-up entry as overlay #2
near the word. Both overlays live in mpv's own OSD surface → fullscreen-safe.
"""

from __future__ import annotations

import logging
import queue
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from overlay import otel_metrics
from overlay.app import (
    analysis_overlay,
    backlog,
    help_overlay,
    hover_snapshot,
    miner_ui,
    nested_popup,
    prefetch,
    reader_deps,
    session_stats,
    sidebar,
    subnav,
    subtitle_modes,
    telemetry,
    tooltip,
    translation,
)
from overlay.app.bindings import (
    ANALYSIS_MSG,
    ANNOTATION_MSG,
    BOOKMARK_MSG,
    CLICK_MSG,
    COPY_CLICK_MSG,
    COPY_LINE_MSG,
    COPY_MSG,
    HELP_CLOSE_MSG,
    HELP_MESSAGES,
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
    SCROLL_DOWN_MSG,
    SCROLL_UP_MSG,
    SIDEBAR_MSG,
    SPEAK_MSG,
    SUB_DELAY_MINUS_MSG,
    SUB_DELAY_PLUS_MSG,
    SUB_DELAY_RESET_MSG,
    SUB_NEXT_MSG,
    SUB_PREV_MSG,
    SUB_REPLAY_MSG,
    SUBTITLE_LANGUAGE_MSG,
    SUBTITLE_RETRY_MSG,
    TIP_CLOSE_MSG,
    TIP_DOWN_MSG,
    TIP_UP_MSG,
    TRANS_MSG,
    active_bindings,
)
from overlay.app.config import ReaderOptions
from overlay.app.media import (
    copy_clipboard,
    tts_available,
)
from overlay.app.miner import Miner, tag_slug
from overlay.app.overlay_ids import OverlayId
from overlay.app.perf import gil_disabled
from overlay.app.popups import Panel, PopupView
from overlay.app.reader_context import Delegated, EpisodeContext
from overlay.app.sub_index import SubIndex
from overlay.app.subtitle_render import NullRenderer, SubtitleRenderer
from overlay.app.toast import render_toast
from overlay.app.tokenize import SKIP_POS, Token, inflected_in, merge_dict_compounds, tokenize
from overlay.mpvio.osd import Overlay

if TYPE_CHECKING:
    from overlay.app.card_preview import PreviewData
    from overlay.app.prefetch import RenderAheadReq
    from overlay.app.render_cache import RenderCache
    from overlay.app.session_stats import SessionRecorder
    from overlay.mask_atlas import MaskAtlas
    from overlay.mpvio.ipc import MpvIPC
    from overlay.panel import Freq

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
OBSERVED_PROPS = ("sub-text", "mouse-pos", "osd-dimensions", "pause", "secondary-sub-text", "sid")

# The one-panel crisp path snaps the display scale to this bucket so mpv's osd-dimensions wobble
# reuses cached native bands instead of re-rastering (see Reader._raster_scale).
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
    jp_sid = Delegated[int | None]("episode", "jp_sid")
    en_sid = Delegated[int | None]("episode", "en_sid")
    subtitle_language = Delegated[subtitle_modes.Language]("episode", "subtitle_language")
    subtitle_slang = Delegated[str]("episode", "subtitle_slang")
    _sub_index = Delegated[SubIndex | None]("episode", "sub_index")
    _nav_idx = Delegated[int]("episode", "nav_idx")
    _sub_settle_until = Delegated[float]("episode", "sub_settle_until")
    _nav_prev_text = Delegated[str]("episode", "nav_prev_text")

    def __init__(
        self,
        ipc: MpvIPC,
        scorer=None,
        anki=None,
        mine_cfg=None,
        dict_set=None,
        options: ReaderOptions | None = None,
        renderer: SubtitleRenderer | NullRenderer | None = None,
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
        self.ui_scale = max(0.75, min(2.0, float(o.panels.scale)))
        self.ipc = ipc
        self.ov = Overlay(ipc, id_base=o.overlay_id_base)
        self.renderer = renderer or SubtitleRenderer()  # subtitle raster; NullRenderer() = headless
        self.sub_size_override = o.tooltip.sub_size
        self.bottom_margin_frac = o.tooltip.bottom_margin_frac
        self.scorer = scorer  # app.scoring.Scorer | None — per-word coloring
        self.styles: list | None = None
        self.anki = anki  # app.anki.Anki | None — enables one-key mining
        self.mine_cfg = mine_cfg
        self.dict_set = dict_set  # app.dictionary.DictionarySet | None — multi-dict tooltip
        # Progressive startup: deps loaded on a background thread, injected on the main thread by the
        # poll loop (see load_deps_async / _apply_deps). Until then, subs render plain + a spinner shows.
        self._pending_deps: dict | None = None
        # Set by reader_deps' background Anki watcher when AnkiConnect answers → the poll loop backfills
        # the mined set on the main thread (a not-yet-up Anki must not stall the dep/coloring startup).
        self._pending_anki_seed = False
        self._loading = False
        self._load_frame = 0
        self._load_next = 0.0
        self._miner = Miner(self)  # mining flow (app/miner.py)
        self.mine_key = o.keys.mine_key
        self.mine_video_key = o.keys.mine_video_key
        self.mine_all_key = o.keys.mine_all_key
        self.translate_key = o.keys.translate_key
        self.overlay_toggle_key = o.keys.overlay_toggle_key
        self.subtitle_language_key = o.keys.subtitle_language_key
        self.bookmark_key = o.keys.bookmark_key
        self.sidebar_key = o.keys.sidebar_key
        self.analysis_key = o.keys.analysis_key
        self.annotation_key = o.keys.annotation_key
        self.help_key = o.keys.help_key
        self.subtitle_retry_key = o.keys.subtitle_retry_key
        self.preview_key = o.keys.preview_key
        self.hover_pause_key = o.keys.hover_pause_key
        self.play_audio = o.mining.play_audio
        self.show_preview = o.mining.show_preview  # auto-pop the card-preview panel after a mine
        # 🔊 TTS button is drawn only when the OS has a Japanese voice — else it silently does nothing.
        # Computed once (voices don't change mid-session; tts_available is itself cached).
        self._tts_ok = tts_available()
        # subtitle navigation keys (configurable; defaults match SUB_NAV_DEFAULTS)
        self.sub_prev_key = o.keys.sub_prev_key  # Alt+LEFT  → sub-seek -1 (previous line)
        self.sub_next_key = o.keys.sub_next_key  # Alt+RIGHT → sub-seek  1 (next line)
        self.sub_replay_key = o.keys.sub_replay_key  # Alt+DOWN  → sub-seek  0 (replay current)
        self.tip_max_frac = o.tooltip.tip_max_frac  # BASE tooltip viewport ≤ this frac of the video
        self.nested_max_frac = o.tooltip.nested_max_frac  # nested (scan) popup viewport frac cap
        self.pause_on_tooltip = o.tooltip.pause_on_tooltip  # auto-pause mpv while a tooltip shows
        if o.tooltip.annotation_mode not in ("full", "hover"):
            raise ValueError(f"unknown annotation mode: {o.tooltip.annotation_mode!r}")
        self.annotation_mode = o.tooltip.annotation_mode
        self._annotation_hover = False
        self.hide_delay = o.tooltip.hide_delay  # tooltip linger after the cursor leaves the word
        self.flash_secs = o.tooltip.flash_secs  # "copied" highlight border pulse duration
        self.panel_cache_max = (
            o.tooltip.panel_cache_max
        )  # LRU cap on cached rendered tooltip panels
        self.band_cache_max = o.tooltip.band_cache_max  # LRU cap on retained render bands per panel
        self.raw_band_ceiling = (
            o.tooltip.raw_band_ceiling_mb * 1024 * 1024
        )  # bytes; 0 = always compress
        # Cross-session persistent render cache (#149): opt-in; seeds a cold hover's first viewport from
        # disk for cost-gated (tall) entries. Built lazily on first use so a non-dict session (or the
        # opt-out) never touches disk. render_cache_min_height gates writes to the pathological tail.
        self._render_cache_on = o.tooltip.render_cache
        self._render_cache_max_bytes = o.tooltip.render_cache_max_mb * 1024 * 1024
        self._render_cache_min_height_px = o.tooltip.render_cache_min_height
        self._render_cache_obj: RenderCache | None = None
        self._render_cache_built = False
        self._render_config_sig: str | None = None
        self._render_sig_key: tuple[int, int] | None = None
        self._mask_atlas_on = (
            o.tooltip.mask_atlas
        )  # persistent glyph mask atlas (#149 Tier-1), opt-out
        self._mask_atlas: MaskAtlas | None = (
            None  # write-back handle (kept alive), or None off/no atlas
        )
        # Idle crisp post-render (hi-dpi): after the instant soft upscale, a single background worker
        # re-renders the CURRENT viewport at NATIVE resolution (reusing a native-scale panel across scrolls
        # of the same word) and the poll loop swaps it in — so scrolling stays crisp, not just the first band.
        # One-panel crisp (scale-as-boundary): the ONE reference panel composites at the display scale
        # (native glyph masks over 1× geometry). ``crisp_upscale`` off → soft-only (never native).
        self._crisp_on = o.tooltip.crisp_upscale
        self._tip_scale_override = o.tooltip.tip_scale  # >0 fixes _tip_display_scale (see config)
        self._crisp_miss = (
            ""  # last blit's soft-fallback reason ("" = composited crisp) — telemetry
        )
        self._crisp_pending = (
            False  # a soft first paint is up; poll upgrades to crisp once bands warm
        )
        self._tip_tok: Token | None = (
            None  # the base tooltip's source token (for the crisp re-render)
        )
        self._tip_inflected: str | None = (
            None  # its inflected surface (re-rendered on show AND scroll)
        )
        from overlay.render.layout_backend import backend_label, resolve_backend

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
        self._paused_by_tip = False
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
        self._head_prefetch_q: queue.PriorityQueue = queue.PriorityQueue(
            maxsize=o.perf.head_prefetch_queue_max
        )
        self._head_seq = 0  # tie-breaker so priority-queue items never compare HeadPrefetchItems
        self._head_built = 0  # a speculative head-render job actually ran to completion
        self._cache_lock = (
            threading.Lock()
        )  # tiny lock: only the cache dict mutation (build is lock-free)
        self._prefetch_q: queue.Queue = queue.Queue()
        self._prefetch_gen = 0  # bumped on line change / resume / seek → cancels in-flight
        # Scroll-ahead: a single slot (newest scroll wins) the prefetch worker drains to render the
        # blocks just beyond the visible tooltip OFF the main thread, so the next notch composites a
        # warm block instead of rasterising it on the scroll frame. Guarded by its own tiny lock.
        self._render_ahead_req: RenderAheadReq | None = None
        self._render_ahead_lock = threading.Lock()
        self._prefetch_key: tuple[str, bool] | None = None
        self._mouse_in = False  # cursor over the video window — an engagement signal
        self._hit_test_tick = 0  # samples the OTel hit-test histogram every _HIT_TEST_SAMPLE_EVERY
        self._scrolled_this_tick = False  # a wheel/tip-scroll ran this poll tick — for render-span
        # attribution (did hover-driven scan/nested-popup work land in the same tick as a scroll?)
        self._tip_show_cold = False  # was the last base-tooltip show a panel build (vs a cache hit)
        self._runtime_announced = (
            False  # the runtime banner prints once, after prefetch actually starts
        )
        self._stop = threading.Event()
        self._prefetch_threads: list[threading.Thread] = []
        # translation reveal: manual toggle (`t`), or auto-reveal on hover when opted in.
        # Auto keeps the anti-crutch spirit — the EN only appears while you're actively looking a
        # word up (a tooltip is shown), not for every line you already understand.
        self.auto_translate = o.translation.auto_translate
        self._translate_on = False
        self._trans_text: str | None = None
        self._translation_secondary_sid: int | None = None
        self._last_announced_sid: int | None = None
        self._overlay_mpv_state: dict[str, object] | None = None
        self._subtitle_results: queue.SimpleQueue = queue.SimpleQueue()
        self._subtitle_fetch_threads: list[threading.Thread] = []
        self._subtitle_retry_factory: subtitle_modes.ProviderFetchFactory | None = None
        self._subtitle_retry_active = False
        self._subtitle_retry_lock = threading.Lock()
        self._backlog_store: backlog.BacklogStore | None = None
        self.sidebar = sidebar.SidebarState()
        self.analysis = analysis_overlay.AnalysisState()
        self._session_recorder: SessionRecorder | None = None
        self._help_open = False
        self._help_page = 0
        self._last_jpg: Path | None = None
        self._last_audio: Path | str | None = None
        self._last_preview: PreviewData | None = None
        self._mined: set[str] = set()  # card expressions already in the deck → header ⊕ becomes ✓
        self._anki_cache: tuple[float, bool] = (
            0.0,
            False,
        )  # (checked_at, reachable) — see _anki_ok
        # card-preview interaction (clickable regions in screen coords; None when hidden)
        self._preview_rect: tuple | None = None
        # Forced mouse-section state (see _sync_mouse_capture).
        self._mouse_section_defined = False
        self._mouse_captured = False
        self._mouse_reassert_at = 0.0
        self._preview_close_rect: tuple | None = None
        self._preview_audio_rect: tuple | None = None
        self._preview_image_rect: tuple | None = None
        self._preview_dup_rect: tuple | None = None
        self._dup_tok: Token | None = None  # token behind an "exists" preview, for "add anyway"
        self._preview_zoom = False  # the screenshot is enlarged (toggled by clicking it)
        self._tip_rect: tuple | None = (
            None  # (x, y, w, h) of the visible tooltip, for hover keep-alive
        )
        self._hide_at = 0.0  # monotonic time to hide the tooltip (0 = not scheduled)
        self._tip_scroll = 0
        self._tip_view_h = 0
        self._tip_xy: tuple[int, int] = (0, 0)
        self._tip_state: Panel | None = None  # Panel currently shown
        self._tip_key: tooltip.PanelKey | None = None  # its cache key
        # Yomitan-style in-place link navigation: clicking a cross-reference replaces the base
        # tooltip's content and pushes the previous view here; Esc/back pops it. Cleared when hovering
        # a new subtitle word (show_tooltip_impl) or on teardown. Empty ⇒ the base is a hovered word.
        self._tip_nav: list = []
        self._nest = _Nested()  # nested scan popup (hover a word inside the tooltip → its entry)
        # Yomitan-style scan delay: the cursor must dwell on a word inside the tooltip before its
        # popup opens, so drifting across the definition doesn't fire a flurry of popups.
        self.scan_delay = o.tooltip.scan_delay
        self._scan_target: str | None = (
            None  # the scan-cell tail the cursor is currently settling on
        )
        self._scan_since = 0.0  # when it became the target (dwell start)
        # subtitle-word switch dwell: transiting the cursor over other words (e.g. the other line of a
        # two-line sub) on the way to the tooltip must not switch it — only resting on a new word does.
        self.hover_switch_delay = o.tooltip.hover_switch_delay
        self._word_target: int | None = None
        self._word_since = 0.0
        self._last_mouse = (
            -1.0,
            -1.0,
        )  # latest cursor pos — routes the wheel to the popup under it
        self._flash_oid: int | None = (
            None  # a popup pulsing a "copied" highlight border (TIP_ID / NESTED_ID)
        )
        self._flash_until = 0.0
        self._hover_reading = ""  # dict-form reading of the hovered word, for TTS
        # Multi-token dictionary terms starting at the hovered word (数ある over 数), longest-first, and
        # the token span the longest covers: the tooltip stacks them above the bare word and the
        # underline spans the match. Empty / None when no longer term starts here.
        self._hover_terms: tuple[str, ...] = ()
        self._hover_span: tuple[int, int] | None = None
        self._kanji_index = 0  # `k` cycles the hovered word's kanji
        self._tip_keys_bound = False
        # LRU cache: OrderedDict keyed by panel_key, bounded at panel_cache_max entries. Each Panel
        # retains only its windowed engine's blocks (zlib-compressed, bounded to the last viewport±
        # overscan), so the whole cache stays small. On overflow we evict the LEAST-recently-used entry
        # (the OrderedDict move_to_end protocol) rather than clearing everything (which would lose
        # already-rendered panels the user is likely to re-hover).
        self._panel_cache: OrderedDict = OrderedDict()  # key -> Panel
        self._tmp = Path(tempfile.mkdtemp(prefix="saitenka-mine-"))
        self._toast_until = 0.0
        # Event-driven property state (observe_property); empty + off until run() calls
        # start_observing(), so direct get_property keeps working for tests / pre-run paths.
        self._observing = False
        self._observed: dict = {}
        self.osd = (1280, 720)
        # subtitle state (populated by set_subtitle; initialised for the live run() path)
        self._first_sub_logged = False  # gates the one-time "first subtitle drawn" info log
        self.sub_text = ""
        self.lines: list[list[Token]] = []
        self.tokens: list[Token] = []
        self.boxes: list = []
        self.sub_origin: tuple[int, int] = (0, 0)
        self.hover = -1
        self._nudge_pending = (
            False  # a draw happened while paused → re-flush the OSD next tick (#8172)
        )

    def hover_view(self) -> hover_snapshot.HoverView:
        """Read-only snapshot of the hover stack (nested popup / tooltip / pause / nav / scan) —
        the public seam tests observe instead of the private ``_nest`` / ``_tip_*`` fields (#43)."""
        return hover_snapshot.snapshot(self)

    # scale subtitle/tooltip to the video size (the user usually watches 1080p)
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
    def bottom_margin(self) -> int:
        return round(self.osd[1] * self.bottom_margin_frac)

    # --- mpv property helpers -----------------------------------------------------------------
    def _get(self, prop):
        return self.ipc.command("get_property", prop).get("data")

    def start_observing(self) -> None:
        """Register ``observe_property`` for the hot-path properties and seed their initial values
        with ONE get_property each. After this, the poll loop consumes buffered ``property-change``
        events instead of doing blocking round-trips every tick. Main-thread only (IPC)."""
        for i, name in enumerate(OBSERVED_PROPS, 1):
            self.ipc.command("observe_property", i, name)
            self._observed[name] = self._get(name)  # initial state (pre-observe)
        self._observing = True
        # Seed values are the first sign the mpv→client read path works: a None osd-dimensions here
        # (with mpv clearly running) means get_property replies aren't coming back — the pipe read is
        # dead, so nothing will ever draw. Logged so it lands in overlay.log / report.
        log.info(
            "observing mpv props; seed osd-dimensions=%r sub-text=%r",
            self._observed.get("osd-dimensions"),
            self._observed.get("sub-text"),
        )
        if self._observed.get("osd-dimensions") is None:
            log.warning(
                "osd-dimensions seed is None — mpv isn't returning get_property replies (dead pipe / "
                "attached to a not-yet-ready mpv); the overlay won't draw until that recovers"
            )
        else:
            self._probe_display_sources("seed", self._observed.get("osd-dimensions") or {})

    def _prop(self, name: str):
        """Latest value of a property: the observed (event-driven) state when observing, else a
        blocking get_property (tests / pre-run paths)."""
        if self._observing and name in self._observed:
            return self._observed[name]
        return self._get(name)

    def _on_property_change(self, ev: dict) -> None:
        name = ev.get("name")
        if name:
            changed = ev.get("data") != self._observed.get(name)
            if name == "pause" and ev.get("data") != self._observed.get(name):
                # Breadcrumb for the "overlay only updates on mouse move" report: while paused, mpv's
                # d3d11 flip-model VO won't re-present the window on an overlay-add (see the
                # --d3d11-flip=no launch mitigation). Correlate pause spans with overlay draws.
                log.debug("mpv pause -> %s", ev.get("data"))
            self._observed[name] = ev.get("data")
            if name == "sid" and changed:
                subtitle_modes.on_primary_changed(self, ev.get("data"))

    def refresh_osd(self) -> bool:
        d = self._prop("osd-dimensions") or {}
        w, h = int(d.get("w") or self.osd[0]), int(d.get("h") or self.osd[1])
        if (w, h) != self.osd and w > 0 and h > 0:
            self.osd = (w, h)
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
        self.ov.hide(TIP_ID)
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
            self.ipc.command("set_property", "pause", False)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format
            self._paused_by_tip = False
        self._sync_auto_translation()

    def set_subtitle(self, text: str) -> None:
        # Per-cue breadcrumb (low frequency): correlates mpv's sub-text change with the overlay draw +
        # paused-state in the report — the mpv-log-vs-overlay-log gap the paused-OSD bug lives in.
        log.debug("sub-text change: %d chars, paused=%s", len(text.strip()), self._prop("pause"))
        # Seek-to-paint chain: this span covers everything below (teardown/tokenize/score/render/
        # upload) for one cue. Nests as a child of sub_nav's "sub_seek" span for the instant-nav
        # (Alt+←/→/↓) path, or of "sub_text_reconcile" for an mpv-driven change (native sub-seek /
        # normal cue advance) — either way, its duration IS the "seek command → drawn" latency.
        with otel_metrics.instrumented(otel_metrics.cue_redraw_duration_ms, "cue_redraw"):
            self._set_subtitle_inner(text)

    def _set_subtitle_inner(self, text: str) -> None:
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
        self._nav_idx = -1  # any external cause of a cue change invalidates the nav chaining hint
        with otel_metrics.traced("hide_preview"):
            self._hide_preview()  # a new cue → dismiss the last card preview
        if not text.strip():
            self.lines, self.tokens, self.boxes = [], [], []
            self.ov.hide(SUB_ID)
            self.ov.hide(TIP_ID)
            return
        if self._session_recorder is not None:
            self._session_recorder.record_cue(
                (
                    self.subtitle_language,
                    self._prop("sub-start"),
                    self._prop("sub-end"),
                    text,
                )
            )
        if self.subtitle_language == "en":
            self.lines, self.tokens, self.styles = [], [], None
            self._draw_subtitle()
            return
        # honour explicit line breaks (\n, ASS \N); tokenize each source line separately
        norm = text.replace("\\N", "\n").replace("\r", "")
        # Dictionary-attested compound merge (応急+処置 → 応急処置) — one hover/color/mine unit like
        # Yomitan. Optional dict capability, absent until the dicts finish loading (like has_term).
        exists = getattr(self.dict_set, "terms_exist", None)
        with otel_metrics.traced("tokenize_line", chars=str(len(norm))):
            lines = (tokenize(ln) for ln in norm.split("\n") if ln.strip())
            self.lines = [merge_dict_compounds(t, exists) if exists else t for t in lines]
        self.tokens = [t for line in self.lines for t in line]
        # score the whole cue (N+1 splits by sentence punctuation across lines); warms lookup cache
        with otel_metrics.traced("score_line"):
            self.styles = self.scorer.score_line(self.tokens) if self.scorer else None
        self._draw_subtitle()

    def _draw_subtitle(self) -> None:
        self.renderer.draw(self)

    # --- hover --------------------------------------------------------------------------------
    def _hit(self, mx: float, my: float) -> int:
        ox, oy = self.sub_origin
        for b in self.boxes:
            tok = self.tokens[b.index]
            if tok.pos in SKIP_POS or not tok.surface.strip():
                continue
            if b.contains(mx - ox, my - oy):
                return b.index
        return -1

    @staticmethod
    def _in_rect(rect, x: float, y: float) -> bool:
        rx, ry, rw, rh = rect
        return rx <= x < rx + rw and ry <= y < ry + rh

    def _update_hover(self) -> None:
        if (
            not getattr(self.ov, "visible", True)
            or help_overlay.suppress_hover(self)
            or sidebar.suppress_hover(self)
        ):
            return
        tooltip.update_hover(self)

    def set_hover(self, index: int) -> None:
        tooltip.set_hover(self, index)

    def set_annotation_hover(self, *, revealed: bool) -> None:
        target = bool(
            revealed
            and self.annotation_mode == "hover"
            and self.subtitle_language == "jp"
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
        if not self.lines:
            self._toast("no line to copy", "warn", 1.2)
            return
        copy_clipboard("\n".join(self._sentence_lines()))
        self._toast("copied line", "ok", 1.2)

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
        if sidebar.on_click(self, mp.get("x", -1), mp.get("y", -1)):
            return
        tooltip.on_click(self)

    def _panel_key(
        self, tok, inflected, *, mined: bool = False, phrase: tuple[str, ...] = ()
    ) -> tooltip.PanelKey:
        return tooltip.panel_key(self, tok, inflected, mined=mined, phrase=phrase)

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
    ):
        return tooltip.panel_for(
            self, tok, inflected, min_h, mined=mined, nested=nested, extra_terms=extra_terms
        )

    def _panel_cache_setdefault(self, key, st) -> Panel:
        return tooltip.panel_cache_setdefault(self, key, st)

    # --- persistent render cache (#149): seed a cold hover's first viewport from disk ----------
    def _render_cache(self) -> RenderCache | None:
        """The cross-session render cache, USED WHEN AVAILABLE: opened lazily only if a prebuilt
        ``render-cache.sqlite`` already exists (``saitenka prewarm`` builds it). ``None`` when opted out,
        no dict set, or no prebuilt cache — so a fresh install creates nothing and costs nothing."""
        if not self._render_cache_on or self.dict_set is None:
            return None
        if not self._render_cache_built:
            self._render_cache_built = True
            from overlay.app.paths import cache_dir
            from overlay.app.render_cache import RenderCache

            path = cache_dir() / "render-cache.sqlite"
            if path.exists():  # use-when-available — prewarm is the builder, not a live session
                self._render_cache_obj = RenderCache.open(
                    path, max_bytes=self._render_cache_max_bytes
                )
        return self._render_cache_obj

    def _enable_mask_atlas(self) -> None:
        """Install the persistent glyph mask atlas WHEN AVAILABLE (a prebuilt ``mask-atlas.sqlite``
        exists): wire fonts' write-back now, then bulk-load the masks into a shared read dict on a
        background thread and atomically swap it in (never mutating a dict another thread reads). Once at
        session start; no-op when opted out or no prebuilt atlas."""
        import threading

        from overlay import fonts
        from overlay.app.paths import cache_dir
        from overlay.mask_atlas import MaskAtlas

        if not self._mask_atlas_on or self._mask_atlas is not None:
            return
        path = cache_dir() / "mask-atlas.sqlite"
        if not path.exists():  # use-when-available — prewarm builds it
            return
        atlas = MaskAtlas.open(path)
        if atlas is None:
            return
        self._mask_atlas = atlas
        fonts.set_mask_atlas(None, atlas)  # write-back active immediately; reads join once loaded

        def _load() -> None:
            loaded: dict = {}
            n = atlas.load_into(loaded)
            fonts.set_mask_atlas(
                loaded, atlas
            )  # atomic swap → glyph_mask sees the whole dict at once
            log.info("mask atlas: loaded %d masks", n)

        threading.Thread(target=_load, name="saitenka-mask-atlas-load", daemon=True).start()

    def _render_cache_sig(self) -> str:
        """The current ``config_sig`` (format+width+cap+dict-set), memoised per (width, cap) so a
        resolution change recomputes it. Only called when the cache is on (dict_set present)."""
        cap = self._tip_cap()
        ck = (self.tip_width, cap)
        if self._render_config_sig is None or self._render_sig_key != ck:
            from overlay.app.render_cache import config_signature, dict_set_signature

            assert (
                self.dict_set is not None
            )  # _render_cache() gated on it before any caller reaches here
            self._render_config_sig = config_signature(
                width=self.tip_width, cap=cap, dict_sig=dict_set_signature(self.dict_set)
            )
            self._render_sig_key = ck
        return self._render_config_sig

    def _render_cache_min_height(self) -> int:
        """Cost gate (px): only heads at least this tall — a non-trivial entry that needs scrolling, the
        pathological tail whose cold build+raster blows the budget — are persisted."""
        return self._render_cache_min_height_px

    def _peek_render_cache(self, key):
        """The stored first viewport + ``full_h`` for ``key`` (direct-paint path), or ``None``. Lets a
        cold pathological show paint the cached pixels + place by ``full_h`` BEFORE building the panel."""
        cache = self._render_cache()
        if cache is None:
            return None
        from overlay.app.render_cache import content_key

        return cache.peek(self._render_cache_sig(), content_key(key))

    def _seed_precomposed(self, st: Panel, key, cap: int) -> bool:
        """Seed ``st``'s first viewport from the persistent cache (cold-show fast path). ``False`` when
        the cache is off or misses. Main thread — one indexed SELECT + inflate on a hit."""
        cache = self._render_cache()
        if cache is None:
            return False
        from overlay.app.render_cache import content_key

        return st.load_precomposed_head(cap, cache, self._render_cache_sig(), content_key(key))

    def _precompose_head(
        self, st: Panel, tok, inflected, *, mined: bool, cap: int, protected: bool = False
    ) -> None:
        """Precompose ``st``'s first viewport in idle (the prefetch worker path) and, when the persistent
        cache is on, write a cost-gated head to disk for a later session's cold hover. ``protected`` (the
        offline prewarm) marks the popular set eviction-last so live write-back can't thrash it."""
        cache = self._render_cache()
        if cache is None:
            st.precompose_head(cap)
            return
        from overlay.app.render_cache import content_key

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
        prefetch.update_prefetch(self)

    def _upcoming_cue_texts(self, n: int) -> list[str]:
        return prefetch.upcoming_cue_texts(self, n)

    def _inflected_surface(self, index: int) -> str:
        return inflected_in(self.tokens, index)

    def _telemetry_gauges(self) -> dict[str, float]:
        """Live cache-size gauges for the telemetry interval sampler (writer thread, ~1s cadence — NOT
        the hot path). ``panel_cache.bytes`` is the retained (compressed) on-heap footprint;
        ``dict_cache.size`` the decoded-entry count across every dictionary. Read under ``_cache_lock``
        so a concurrent prefetch worker mutating the panel cache can't fault the iteration."""
        with self._cache_lock:
            panel_n = len(self._panel_cache)
            panel_bytes = sum(st.retained_nbytes for st in self._panel_cache.values())
        dict_n = self.dict_set.decoded_entry_count() if self.dict_set is not None else 0
        return {
            "panel_cache.size": float(panel_n),
            "panel_cache.bytes": float(panel_bytes),
            "dict_cache.size": float(dict_n),
        }

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

    def _blit_crisp_or_soft(self, panel, key, scroll: int, view_h: int, xy, oid: int):
        # Crisp-from-native-cache-when-built, else soft upscale (the SSOT both popups blit through).
        # Delegated here so nested_popup reaches it via the Reader seam, not a nested_popup→tooltip
        # import (which would cycle — tooltip already imports nested_popup for TIP_GAP).
        return tooltip._blit_crisp_or_soft(self, panel, key, scroll, view_h, xy, oid)

    def _bind_tip_keys(self) -> None:
        """Register the tooltip-scoped keys (idempotent — word switches must not re-bind)."""
        if self._tip_keys_bound:
            return
        self._tip_keys_bound = True
        if self._help_open:
            return
        for binding in active_bindings(self, "tooltip"):
            self.ipc.command("keybind", binding.key, f"script-message {binding.spec.message}")

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
            self.ipc.command(
                "keybind", binding.key, "ignore"
            )  # valid no-op; "" would be rejected by mpv

    def _define_mouse_section(self) -> None:
        """Define (once) the FORCED mpv section for the ``mouse``-scoped bindings; once enabled it
        outranks other scripts' forced MBTN_LEFT (uosc/inputevent). Enabled per _sync_mouse_capture."""
        lines = [f"{b.key} script-message {b.spec.message}" for b in active_bindings(self, "mouse")]
        self._mouse_section_defined = bool(lines)
        if lines:
            self.ipc.command("define-section", MOUSE_SECTION, "\n".join(lines) + "\n", "force")

    def _wants_mouse_capture(self) -> bool:
        return (
            self._tip_rect is not None
            or self.sidebar.open
            or self._help_open
            or self._preview_rect is not None
        )

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
        nested_popup.render_nested_view(self)

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
        nested_popup.scroll_nested(self, delta)

    # --- nested scanning: hover a word INSIDE the tooltip → its own popup -----------------------
    def _scan_hit(self, mx: float, my: float):
        return tooltip.scan_hit(self, mx, my)

    def _show_nested(self, sb) -> None:
        nested_popup.show_nested(self, sb)

    def _open_nested(self, tok, inflected, wx: float, wy: float, wh: float, tail=None) -> None:
        nested_popup.open_nested(self, tok, inflected, wx, wy, wh, tail)

    def _place_nested(
        self, st, key, token, word: str, wx: float, wy: float, wh: float, tail=None
    ) -> None:
        nested_popup.place_nested(self, st, key, token, word, wx, wy, wh, tail)

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

    def _tip_close_or_back(self) -> None:
        """Esc while link-navigated steps back one entry; at the root (or a plain hovered word) it
        closes the tooltip — the browser-back-then-close feel Yomitan's history gives."""
        if not tooltip.tip_back(self):
            self.set_hover(-1)

    def _open_search(self, pattern: str, wx: float, wy: float, wh: float) -> None:
        nested_popup.open_search(self, pattern, wx, wy, wh)

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
        from overlay.app.miner import source_meta

        return source_meta(video)

    def _provenance(self, video) -> str:
        return self._miner.provenance(video)

    def _mine_tags(self, video) -> list[str]:
        return self._miner.mine_tags(video)

    def mine_current(self, *, animated: bool | None = None) -> None:
        if not self.anki or not self.mine_cfg:
            return
        idx = self._mine_target()
        if idx is None:
            self._toast("no word to mine", "warn")
            return
        with otel_metrics.traced("anki_mine", source="base"):
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
        if self._dup_tok is not None:
            self._miner.mine_token(self._dup_tok, force=True)

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
        translation.toggle_translation(self)

    def toggle_overlay(self) -> None:
        if self.ov.visible:
            self._overlay_mpv_state = {
                "sub-visibility": self._get("sub-visibility"),
                "osd-level": self._get("osd-level"),
            }
            self.hover = -1
            self._teardown_tip()
            self.ov.set_visible(visible=False)
            subtitle_modes.release_secondary(self)
            self.ipc.command(
                "set_property",
                "sub-visibility",
                True,  # noqa: FBT003  # mpv IPC wire value
            )
            self.ipc.command("set_property", "osd-level", 1)
            return
        for name, value in (self._overlay_mpv_state or {}).items():
            if value is not None:
                self.ipc.command("set_property", name, value)
        self._overlay_mpv_state = None
        self.ov.set_visible(visible=True)
        if self._translation_visible():
            self._setup_secondary()
            self._draw_translation()

    def configure_subtitle_mode(
        self, startup: subtitle_modes.SubtitleStartup, *, slang: str = "ja,jpn,jp"
    ) -> None:
        subtitle_modes.configure(self, startup, slang=slang)

    def toggle_subtitle_language(self) -> None:
        subtitle_modes.toggle(self)

    def fetch_japanese_subs_async(self, fetch) -> None:
        subtitle_modes.start_fetch(self, fetch, select_if_unchanged=True)

    def configure_subtitle_retry(self, factory) -> None:
        subtitle_modes.configure_retry(self, factory)

    def retry_japanese_subtitles(self) -> None:
        subtitle_modes.retry(self)

    def _secondary_text(self) -> str:
        return translation.secondary_text(self)

    def _draw_translation(self) -> None:
        translation.draw_translation(self)

    def _toast(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        img = render_toast(text, kind)
        x = (self.osd[0] - img.width) // 2
        y = round(self.osd[1] * 0.08)
        self.ov.show(img, x, y, oid=TOAST_ID)
        self._toast_until = time.monotonic() + seconds

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

    def toggle_analysis(self) -> None:
        analysis_overlay.toggle(self)

    def toggle_annotation_mode(self) -> None:
        self.annotation_mode = "hover" if self.annotation_mode == "full" else "full"
        self._annotation_hover = False
        if self.sub_text.strip():
            self._draw_subtitle()
        label = "full" if self.annotation_mode == "full" else "hover-only"
        self._toast(f"annotations: {label}")

    def toggle_help(self) -> None:
        help_overlay.toggle(self)

    def _register_keybinds(self) -> None:
        # mpv `keybind` takes the command as ONE string, e.g. "script-message saitenka-speak".
        # CRITICAL: passing the command as split args silently kills the key — always one string.
        def bind(key: str, msg: str) -> None:
            self.ipc.command("keybind", key, f"script-message {msg}")

        for binding in active_bindings(self, "global"):
            message = binding.spec.message
            if message is not None:
                bind(binding.key, message)
        self._define_mouse_section()  # "mouse"-scoped controls live in a forced section, enabled on demand

    # msg -> handler(reader). Subtitle-nav entries render the target cue from the index INSTANTLY
    # (if we have one), then issue the real sub-seek so the video catches up behind it (read the
    # position first: _sub_nav samples sub-start/time-pos before the seek moves them).
    _HANDLERS: ClassVar[dict] = {
        MINE_MSG: lambda r: r.mine_current(),
        MINE_VIDEO_MSG: lambda r: r.mine_current_video(),
        MINE_ALL_MSG: lambda r: r.bulk_mine(),
        TRANS_MSG: lambda r: r.toggle_translation(),
        OVERLAY_TOGGLE_MSG: lambda r: r.toggle_overlay(),
        SUBTITLE_LANGUAGE_MSG: lambda r: r.toggle_subtitle_language(),
        SUBTITLE_RETRY_MSG: lambda r: r.retry_japanese_subtitles(),
        HOVER_PAUSE_MSG: lambda r: r.toggle_hover_pause(),
        BOOKMARK_MSG: lambda r: r.toggle_bookmark(),
        SIDEBAR_MSG: lambda r: r.toggle_sidebar(),
        ANALYSIS_MSG: lambda r: r.toggle_analysis(),
        ANNOTATION_MSG: lambda r: r.toggle_annotation_mode(),
        HELP_TOGGLE_MSG: lambda r: r.toggle_help(),
        HELP_PREV_MSG: lambda r: help_overlay.step(r, -1),
        HELP_NEXT_MSG: lambda r: help_overlay.step(r, 1),
        HELP_CLOSE_MSG: lambda r: help_overlay.close_help(r),
        PREVIEW_MSG: lambda r: r.replay_preview(),
        PREVIEW_CLOSE_MSG: lambda r: r._hide_preview(),
        SCROLL_UP_MSG: lambda r: r._scroll_tip(-round(r._tip_ref_h * 0.12)),
        SCROLL_DOWN_MSG: lambda r: r._scroll_tip(round(r._tip_ref_h * 0.12)),
        SPEAK_MSG: lambda r: r.speak_hovered(),
        COPY_MSG: lambda r: r.copy_hovered(),
        COPY_LINE_MSG: lambda r: r.copy_line(),
        COPY_CLICK_MSG: lambda r: r.copy_click(),
        CLICK_MSG: lambda r: r.on_click(),
        SUB_PREV_MSG: lambda r: (r._sub_nav(-1), r.ipc.command("sub-seek", "-1")),
        SUB_NEXT_MSG: lambda r: (r._sub_nav(1), r.ipc.command("sub-seek", "1")),
        SUB_REPLAY_MSG: lambda r: (r._sub_nav(0), r.ipc.command("sub-seek", "0")),
        SUB_DELAY_MINUS_MSG: lambda r: r.ipc.command("add", "sub-delay", "-0.1"),
        SUB_DELAY_PLUS_MSG: lambda r: r.ipc.command("add", "sub-delay", "0.1"),
        KANJI_MSG: lambda r: r.kanji_current(),
        TIP_UP_MSG: lambda r: r._scroll_tip(-round(r._tip_ref_h * 0.12)),
        TIP_DOWN_MSG: lambda r: r._scroll_tip(round(r._tip_ref_h * 0.12)),
        TIP_CLOSE_MSG: lambda r: r._tip_close_or_back(),
        SUB_DELAY_RESET_MSG: lambda r: r.ipc.command("set_property", "sub-delay", "0"),
    }

    def _handle(self, msg: str) -> None:
        if self._help_open and msg not in HELP_MESSAGES:
            return
        handler = self._HANDLERS.get(msg)
        if handler:
            handler(self)

    # --- run loop -----------------------------------------------------------------------------
    def poll_once(self) -> bool:
        """One tick: sync subtitle + hover, handle key events. False if mpv went away."""
        try:
            self._scrolled_this_tick = False  # set by _scroll_tip below (wheel or TIP_UP/DOWN)
            self.ipc.pump()  # sole socket reader in steady state: fetch events, detect mpv quit
            session_stats.tick(self)
            self._flush_paused_nudge()
            ops_before = self.ov.ops
            scroll_steps = self._drain_events()
            if (
                scroll_steps
                and not help_overlay.scroll(self, scroll_steps)
                and not sidebar.scroll(self, scroll_steps)
            ):
                self._scroll_tip(scroll_steps * round(self._tip_ref_h * 0.14))
            self._expire_toast()
            self._expire_flash()
            if self.refresh_osd():
                if self.sub_text.strip():
                    self._draw_subtitle()
                help_overlay.redraw(self)
                analysis_overlay.redraw(self)
            self._reconcile_sub_text(self._prop("sub-text") or "")
            self._maybe_log_stall()
            self._apply_pending_deps_or_spinner()
            self._apply_pending_anki_seed()
            tooltip.apply_pending_crisp(self)  # upgrade a soft first paint to crisp once bands warm
            subtitle_modes.apply_fetch_results(self)
            analysis_overlay.apply_results(self)
            sidebar.update(self)
            self._update_hover()
            self._sync_mouse_capture()  # own clicks/wheel while a surface is up (this tick, no gap)
            self._update_prefetch()
            if self._translation_visible() and self._secondary_text() != self._trans_text:
                self._draw_translation()  # keep the (manual or auto) translation current as subs change
            self._schedule_paused_nudge(ops_before)
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

    def _drain_events(self) -> int:
        """Consume this tick's mpv events; returns the net scroll delta (coalesced, not yet applied)."""
        scroll_steps = 0
        for ev in self.ipc.drain_events():
            kind = ev.get("event")
            if kind == "property-change":  # observed state — no round-trips
                self._on_property_change(ev)
            elif kind == "client-message":
                msg = (ev.get("args") or [""])[0]
                if msg == SCROLL_UP_MSG:
                    scroll_steps -= 1  # coalesce a fast wheel spin into ONE re-render
                elif msg == SCROLL_DOWN_MSG:
                    scroll_steps += 1
                else:
                    self._handle(msg)
        return scroll_steps

    def _expire_toast(self) -> None:
        if self._toast_until and time.monotonic() > self._toast_until:
            self.ov.hide(TOAST_ID)
            self._toast_until = 0.0

    def _expire_flash(self) -> None:
        if not (self._flash_until and time.monotonic() >= self._flash_until):
            return
        oid, self._flash_oid, self._flash_until = self._flash_oid, None, 0.0
        if oid == NESTED_ID:
            self._render_nested_view()  # redraw without the highlight border
        elif oid == TIP_ID:
            self._render_tip_view()

    def _apply_pending_deps_or_spinner(self) -> None:
        """Progressive startup: inject background-loaded deps (once), else animate the spinner."""
        if self._pending_deps is not None:
            deps, self._pending_deps = self._pending_deps, None
            self._apply_deps(deps)
        elif self._loading:
            self._draw_loading()

    def _apply_pending_anki_seed(self) -> None:
        """Main-thread hand-off from reader_deps' Anki watcher: backfill the mined set once Anki is up."""
        if self._pending_anki_seed:
            self._pending_anki_seed = False
            self._seed_mined()

    def _schedule_paused_nudge(self, ops_before: int) -> None:
        """An overlay changed while mpv is paused → schedule a re-flush next tick so mpv actually
        presents it (mpv #8172; see Overlay.repaint). Only when paused: playing frames present on
        their own, and re-adding every tick would be wasteful."""
        if self.ov.ops != ops_before and self._prop("pause"):
            self._nudge_pending = True
            if otel_metrics.osd_paused_draw is not None:
                otel_metrics.osd_paused_draw.add(1)

    def _maybe_log_stall(self) -> None:
        """One-time startup diagnostic for 'mpv plays but the overlay can't draw'. The RELIABLE failure
        signal is a dead read direction, NOT missing subtitles: a section can legitimately have no subs
        for minutes (an anime OP), so 'no sub-text' alone must never warn — that was the old
        false-alarm. We WARN only when mpv's replies aren't reaching us — zero bytes ever read (the
        classic Windows named-pipe failure) or osd-dimensions never resolved — because then nothing can
        draw regardless of subtitles. If the pipe is alive but there's simply no cue yet, note it once
        at debug. Lives in overlay.log / report; playback is unaffected."""
        if getattr(self, "_stall_warned", False):
            return
        started = getattr(self, "_run_started", None)
        if started is None or time.monotonic() - started < 8.0:
            return
        self._stall_warned = True
        secs = time.monotonic() - started
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
        self._miner.seed_mined()

    # --- subtitle navigation (instant render, then seek) --------------------------------------
    def load_sub_index(self, path) -> None:
        subnav.load_sub_index(self, path)

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

    def _announce_runtime(self) -> None:
        """Print the runtime banner exactly once, from wherever prefetch actually finishes starting
        (sync: run(); async: apply_deps after start_prefetch). Reports the LIVE worker count — the old
        run()-time print always showed 0 because async deps hadn't spawned the workers yet."""
        if self._runtime_announced:
            return
        self._runtime_announced = True
        mode = "free-threaded (GIL off)" if gil_disabled() else "GIL"
        print(  # noqa: T201  # user-facing banner; log.info alone won't show — console handler is WARNING-level (logsetup.py)
            f"[saitenka] runtime: {mode} · {len(self._prefetch_threads)} prefetch worker(s)"
        )
        log.info("runtime: %s, %d prefetch worker(s)", mode, len(self._prefetch_threads))

    def run(self, interval: float | None = None) -> None:
        from overlay.render.banded import guard_main_render

        guard_main_render(
            on=True
        )  # this IS the render loop — native rasterisation must run on a worker
        interval = interval if interval is not None else self.poll_interval
        self.refresh_osd()
        self.start_observing()  # event-driven property reads from here on
        self._register_keybinds()
        self._seed_mined()
        self.start_prefetch()
        self._enable_mask_atlas()  # load a prebuilt glyph mask atlas (bg) if one exists — #149 Tier-1
        session_stats.start(self)
        telemetry.set_gauge_provider(self._telemetry_gauges)  # no-op unless telemetry is configured
        # In run/attach the deps (and thus prefetch workers) load ASYNC — dict_set is still None here,
        # so start_prefetch above was a no-op and the worker count is 0. Defer the banner to when
        # prefetch actually starts (apply_deps → _announce_runtime); only announce now on the sync path
        # (deps already present, e.g. a demo/screenshot run) where apply_deps is never called.
        if self.dict_set is not None:
            self._announce_runtime()
        self._run_started = time.monotonic()  # baseline for the no-subtitle stall diagnostic
        self._stall_warned = False
        while self.poll_once():
            time.sleep(interval)

    def close(self) -> None:
        import shutil

        self._release_mouse_capture()  # hand the mouse back before a detached mpv outlives us
        telemetry.set_gauge_provider(None)  # drop our cache-gauge closure before teardown
        self._stop.set()  # signal the workers; they do no IPC so this is race-free
        for th in self._prefetch_threads:
            th.join(timeout=2.0)  # daemon threads → process can exit even if one is stuck
        for th in self._subtitle_fetch_threads:
            th.join(timeout=2.0)
        for th in self.analysis.threads:
            th.join(timeout=2.0)
        stats_summary = session_stats.finish(self)
        if stats_summary and self.options.stats.summary:
            print(f"[saitenka] session: {stats_summary}")  # noqa: T201  # requested close summary
        if self._backlog_store is not None:
            self._backlog_store.close()
        self.ov.close()
        shutil.rmtree(self._tmp, ignore_errors=True)  # clean up the per-session scratch dir
