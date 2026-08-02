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
from overlay.app.subtitle_render import NullRenderer, SubtitleRenderer
from overlay.app.toast import render_toast
from overlay.app.tokenize import SKIP_POS, Token, inflected_in, tokenize
from overlay.mpvio.osd import Overlay

if TYPE_CHECKING:
    from overlay.app.card_preview import PreviewData
    from overlay.app.session_stats import SessionRecorder
    from overlay.app.sub_index import SubIndex
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


# Popup view/panel classes live in app/popups.py; the _Nested alias is kept because the controller
# internals and the test-suite reference the old private name.
_Nested = PopupView


class Reader:
    """Owns the reader loop (see module docstring): subtitle draw → hover hit-test → tooltip → mine."""

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
        self.jp_sid: int | None = None
        self.en_sid: int | None = None
        self.subtitle_language: subtitle_modes.Language = "jp"
        self.subtitle_slang = "ja,jpn,jp"
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
        # subtitle navigation: an index of the external sub file's cues (when known) lets Alt+←/→/↓
        # render the target line in the overlay INSTANTLY, decoupled from mpv's slow video seek. The
        # real sub-seek still fires behind it and reconciles once it settles (see _sub_nav).
        self._sub_index: SubIndex | None = None
        self._nav_idx = -1  # last cue index we jumped to (chaining hint; -1 = unknown)
        self._sub_settle_until = 0.0  # while >now, ignore transient-empty sub-text during a seek
        self._nav_prev_text = ""  # the cue text showing right before a nav render (see reconcile)
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
        # wider than before so the frequency pill row fits on fewer lines (SubMiner-like proportions)
        return int(min(self.osd[0] * 0.36, 640))

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
            return True
        return False

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
        with otel_metrics.traced("tokenize_line", chars=str(len(norm))):
            self.lines = [tokenize(ln) for ln in norm.split("\n") if ln.strip()]
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

    def _panel_key(self, tok, inflected, *, mined: bool = False) -> tooltip.PanelKey:
        return tooltip.panel_key(self, tok, inflected, mined=mined)

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
    ):
        return tooltip.panel_for(self, tok, inflected, min_h, mined=mined, nested=nested)

    def _panel_cache_setdefault(self, key, st) -> Panel:
        return tooltip.panel_cache_setdefault(self, key, st)

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
        ):
            tooltip.scroll_tip(self, delta)

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
    @staticmethod
    def _link_hit(mx: float, my: float, state, xy, scroll: int):
        return nested_popup.link_hit(mx, my, state, xy, scroll)

    def _open_link(self, lb, xy, scroll: int) -> None:
        nested_popup.open_link(self, lb, xy, scroll)

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

    def mine_current(self) -> None:
        if not self.anki or not self.mine_cfg:
            return
        idx = self._mine_target()
        if idx is None:
            self._toast("no word to mine", "warn")
            return
        with otel_metrics.traced("anki_mine", source="base"):
            self._miner.mine_token(self.tokens[idx])

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
        miner_ui.preview_mined(self, card, tok, video, status)

    def _add_duplicate(self) -> None:
        """The preview's ＋ button: mine a second card for the current scene even though the
        expression is already in the deck (a different line/episode/anime)."""
        if self._dup_tok is not None:
            self._miner.mine_token(self._dup_tok, force=True)

    def _preview_existing(self, note_id: int, card, status: str) -> None:
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

    # msg -> handler(reader). Subtitle-nav entries render the target cue from the index INSTANTLY
    # (if we have one), then issue the real sub-seek so the video catches up behind it (read the
    # position first: _sub_nav samples sub-start/time-pos before the seek moves them).
    _HANDLERS: ClassVar[dict] = {
        MINE_MSG: lambda r: r.mine_current(),
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
        SCROLL_UP_MSG: lambda r: r._scroll_tip(-round(r.osd[1] * 0.12)),
        SCROLL_DOWN_MSG: lambda r: r._scroll_tip(round(r.osd[1] * 0.12)),
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
        TIP_UP_MSG: lambda r: r._scroll_tip(-round(r.osd[1] * 0.12)),
        TIP_DOWN_MSG: lambda r: r._scroll_tip(round(r.osd[1] * 0.12)),
        TIP_CLOSE_MSG: lambda r: r.set_hover(-1),
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
                self._scroll_tip(scroll_steps * round(self.osd[1] * 0.14))
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
            subtitle_modes.apply_fetch_results(self)
            analysis_overlay.apply_results(self)
            sidebar.update(self)
            self._update_hover()
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
        log.debug("paused OSD nudge: re-flushed %d overlay(s)", len(self.ov._live))
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
        interval = interval if interval is not None else self.poll_interval
        self.refresh_osd()
        self.start_observing()  # event-driven property reads from here on
        self._register_keybinds()
        self._seed_mined()
        self.start_prefetch()
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
