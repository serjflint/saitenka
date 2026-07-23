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

import numpy as np

from overlay.app.card_preview import PreviewData
from overlay.app.config import ReaderOptions
from overlay.app import miner_ui, nested_popup, prefetch, tooltip, translation
from overlay.app.miner import Miner, tag_slug
from overlay import otel_metrics
from overlay.app.overlay_ids import OverlayId
from overlay.app.perf import gil_disabled
from overlay.app.popups import PopupView, TipPanel
from overlay.app.sub_index import SubIndex, load_index
from overlay.app.media import (
    copy_clipboard,
    tts_available,
)
from overlay.app.subtitles import render_subtitle
from overlay.app.toast import render_toast
from overlay.app.tokenize import SKIP_POS, Token, tokenize
from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.osd import Overlay
from overlay.panel import (
    Freq,
)

log = logging.getLogger(__name__)

# The nested popup gets its own (roomier) height cap (TooltipOptions.nested_max_frac) so shrinking
# the base tooltip (tip_max_frac) doesn't cramp the deep-dive; the nested popup also carries no
# dict-tab strip / reserve (space-saving).
MINE_MSG = "saitenka-mine"
MINE_ALL_MSG = "saitenka-mine-all"
TRANS_MSG = "saitenka-translate"
PREVIEW_MSG = "saitenka-preview"
SCROLL_UP_MSG = "saitenka-scroll-up"
SCROLL_DOWN_MSG = "saitenka-scroll-down"
SPEAK_MSG = "saitenka-speak"
COPY_MSG = "saitenka-copy"
COPY_LINE_MSG = "saitenka-copy-line"
COPY_CLICK_MSG = "saitenka-copy-click"
CLICK_MSG = "saitenka-click"
SUB_PREV_MSG = "saitenka-sub-prev"  # Alt+LEFT → sub-seek -1 (previous subtitle line)
SUB_NEXT_MSG = "saitenka-sub-next"  # Alt+RIGHT → sub-seek 1  (next subtitle line)
SUB_REPLAY_MSG = "saitenka-sub-replay"  # Alt+DOWN → sub-seek 0  (replay current from its start)
SUB_DELAY_MINUS_MSG = "saitenka-sub-delay-minus"  # z → sub-delay nudge −0.1 s
SUB_DELAY_PLUS_MSG = "saitenka-sub-delay-plus"  # Z → sub-delay nudge +0.1 s
SUB_DELAY_RESET_MSG = "saitenka-sub-delay-reset"  # x → sub-delay reset to 0
KANJI_MSG = "saitenka-kanji"  # k → open / cycle the hovered word's kanji
# Tooltip-scoped keys — registered ONLY while a tooltip is visible (bind on show, unbind on hide)
# so mpv keeps its own arrows otherwise. Alt+arrows stay global.
TAB_PREV_MSG = "saitenka-tab-prev"
TAB_NEXT_MSG = "saitenka-tab-next"
TIP_UP_MSG = "saitenka-tip-up"
TIP_DOWN_MSG = "saitenka-tip-down"
TIP_CLOSE_MSG = "saitenka-tip-close"
TIP_KEYBINDS: tuple[tuple[str, str], ...] = (
    ("LEFT", TAB_PREV_MSG),
    ("RIGHT", TAB_NEXT_MSG),
    ("UP", TIP_UP_MSG),
    ("DOWN", TIP_DOWN_MSG),
    ("ESC", TIP_CLOSE_MSG),
)


# Properties the poll loop consumes event-driven (observe_property) instead of issuing 3–5
# blocking get_property round-trips per 25 ms tick. One initial read seeds pre-observe state.
OBSERVED_PROPS = ("sub-text", "mouse-pos", "osd-dimensions", "pause", "secondary-sub-text")


# Popup view/panel classes live in app/popups.py; legacy aliases kept because the controller
# internals and the test-suite reference the old private names.
_TipPanel = TipPanel
_Nested = PopupView


class Reader:
    def __init__(
        self,
        ipc: MpvIPC,
        scorer=None,
        anki=None,
        mine_cfg=None,
        dict_set=None,
        options: ReaderOptions | None = None,
        **legacy_kw,
    ):
        """``options`` is the canonical grouped-knobs object (see app/config.py; a new knob is one
        dataclass field). Legacy exploded kwargs (``mine_key=…``, ``tip_max_frac=…``) are still
        accepted and routed onto the groups; unknown names raise TypeError."""
        o = options or ReaderOptions()
        if legacy_kw:
            o = o.with_overrides(**legacy_kw)
        self.options = o
        self.ipc = ipc
        self.ov = Overlay(ipc, id_base=o.overlay_id_base)
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
        self._loading = False
        self._load_frame = 0
        self._load_next = 0.0
        self._miner = Miner(self)  # mining flow (app/miner.py)
        self.mine_key = o.keys.mine_key
        self.mine_all_key = o.keys.mine_all_key
        self.translate_key = o.keys.translate_key
        self.preview_key = o.keys.preview_key
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
        self.show_dict_tabs = o.tooltip.show_dict_tabs  # draw the sticky dict-tab strip (base only)
        self.pause_on_tooltip = o.tooltip.pause_on_tooltip  # auto-pause mpv while a tooltip shows
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
        self._cache_lock = (
            threading.Lock()
        )  # tiny lock: only the cache dict mutation (build is lock-free)
        self._prefetch_q: queue.Queue = queue.Queue()
        self._finish_q: queue.Queue = (
            queue.Queue()
        )  # high-priority: finish the visible tooltip's tail
        self._prefetch_gen = 0  # bumped on line change / resume / seek → cancels in-flight
        self._prefetch_key: tuple[str, bool] | None = None
        self._mouse_in = False  # cursor over the video window — an engagement signal
        self._hit_test_tick = 0  # samples the OTel hit-test histogram every _HIT_TEST_SAMPLE_EVERY
        self._stop = threading.Event()
        self._prefetch_threads: list[threading.Thread] = []
        # translation reveal: manual toggle (`t`), or auto-reveal on hover when opted in.
        # Auto keeps the anti-crutch spirit — the EN only appears while you're actively looking a
        # word up (a tooltip is shown), not for every line you already understand.
        self.auto_translate = o.translation.auto_translate
        self._translate_on = False
        self._trans_text: str | None = None
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
        self._preview_zoom = False  # the screenshot is enlarged (toggled by clicking it)
        self._tip_rect: tuple | None = (
            None  # (x, y, w, h) of the visible tooltip, for hover keep-alive
        )
        self._hide_at = 0.0  # monotonic time to hide the tooltip (0 = not scheduled)
        self._tip_bgra: np.ndarray | None = (
            None  # active panel decompressed to a premultiplied BGRA array — scroll slices this
        )
        self._tip_scroll = 0
        self._tip_view_h = 0
        self._tip_xy: tuple[int, int] = (0, 0)
        self._tip_state: TipPanel | None = (
            None  # _TipPanel currently shown (viewport-first render may still be filling)
        )
        self._tip_key: tooltip.PanelKey | None = (
            None  # its cache key — the finisher only refreshes the panel still on screen
        )
        self._tip_dirty = (
            False  # a background finish completed the shown panel → re-upload the view
        )
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
            None  # a popup pulsing a "copied" highlight border (OverlayId.TIP / OverlayId.NESTED)
        )
        self._flash_until = 0.0
        self._hover_reading = ""  # dict-form reading of the hovered word, for TTS
        self._kanji_index = 0  # `k` cycles the hovered word's kanji
        # Per-dictionary tabs (sticky row over the tooltip viewport) + tooltip keys
        self._tab_names: list[str] = []
        self._tab_offsets: list[int] = []
        self._tab_rects: list[tuple[int, int, int, int]] = []  # screen coords, for clicks
        self._tab_bgra: np.ndarray | None = None
        self._tab_h = 0
        self._tab_active = -1
        self._tip_keys_bound = False
        # LRU cache: OrderedDict keyed by panel_key, bounded at panel_cache_max entries. Each _TipPanel
        # now holds only a zlib-compressed BGRA blob (~16x on mostly-transparent panels → sub-MB even
        # for a tall multi-dict entry), so the whole cache is tens of MB — we raised the cap from 48 to
        # 128 accordingly. On overflow we evict the LEAST-recently-used entry (the OrderedDict
        # move_to_end protocol) rather than clearing everything (which would lose already-rendered
        # panels the user is likely to re-hover).
        self._panel_cache: OrderedDict = OrderedDict()  # key -> _TipPanel
        self._tmp = Path(tempfile.mkdtemp(prefix="saitenka-mine-"))
        self._toast_until = 0.0
        # Event-driven property state (observe_property); empty + off until run() calls
        # start_observing(), so direct get_property keeps working for tests / pre-run paths.
        self._observing = False
        self._observed: dict = {}
        self.osd = (1280, 720)
        # subtitle state (populated by set_subtitle; initialised for the live run() path)
        self.sub_text = ""
        self.lines: list[list[Token]] = []
        self.tokens: list[Token] = []
        self.boxes: list = []
        self.sub_origin = (0, 0)
        self.hover = -1
        # subtitle navigation: an index of the external sub file's cues (when known) lets Alt+←/→/↓
        # render the target line in the overlay INSTANTLY, decoupled from mpv's slow video seek. The
        # real sub-seek still fires behind it and reconciles once it settles (see _sub_nav).
        self._sub_index: SubIndex | None = None
        self._nav_idx = -1  # last cue index we jumped to (chaining hint; -1 = unknown)
        self._sub_settle_until = 0.0  # while >now, ignore transient-empty sub-text during a seek

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

    def _prop(self, name: str):
        """Latest value of a property: the observed (event-driven) state when observing, else a
        blocking get_property (tests / pre-run paths)."""
        if self._observing and name in self._observed:
            return self._observed[name]
        return self._get(name)

    def _on_property_change(self, ev: dict) -> None:
        name = ev.get("name")
        if name:
            if name == "pause" and ev.get("data") != self._observed.get(name):
                # Breadcrumb for the "overlay only updates on mouse move" report: while paused, mpv's
                # d3d11 flip-model VO won't re-present the window on an overlay-add (see the
                # --d3d11-flip=no launch mitigation). Correlate pause spans with overlay draws.
                log.debug("mpv pause -> %s", ev.get("data"))
            self._observed[name] = ev.get("data")

    def refresh_osd(self) -> bool:
        d = self._prop("osd-dimensions") or {}
        w, h = int(d.get("w") or self.osd[0]), int(d.get("h") or self.osd[1])
        if (w, h) != self.osd and w > 0 and h > 0:
            self.osd = (w, h)
            return True
        return False

    # --- subtitle -----------------------------------------------------------------------------
    def _teardown_tip(self) -> None:
        """Tear down the hover stack unconditionally: hide OverlayId.TIP/OverlayId.NESTED, reset all tooltip
        state, and release any _paused_by_tip. Called by set_hover(-1) AND set_subtitle so that
        a cue change while a tooltip is showing always clears it via the real path — avoiding the
        early-return in set_hover (index == self.hover) that would otherwise short-circuit teardown
        when hover is already -1 but the tip is still on screen."""
        self.ov.hide(OverlayId.TIP)
        self._hide_nested()
        self._tip_rect = None
        self._tip_bgra = None
        self._tip_state = None
        self._tip_key = None
        self._tip_dirty = False
        self._hover_reading = ""
        self._kanji_index = 0
        self._tab_names, self._tab_offsets, self._tab_rects = [], [], []
        self._tab_bgra, self._tab_h, self._tab_active = None, 0, -1
        self._unbind_tip_keys()
        if self._paused_by_tip:
            self.ipc.command("set_property", "pause", False)
            self._paused_by_tip = False
        self._sync_auto_translation()

    def set_subtitle(self, text: str) -> None:
        # Tear down the hover stack via the shared path BEFORE mutating sub_text/hover so that
        # OverlayId.TIP/OverlayId.NESTED are hidden, _tip_rect/_tip_state/_tip_key/_nest are reset, and any
        # _paused_by_tip is released.  We cannot rely on set_hover(-1) here because its
        # early-return (index == self.hover) would skip teardown if hover is already -1 but
        # tip state is present (e.g. _show_tooltip was called directly without set_hover).
        self._teardown_tip()
        self.hover = -1
        self.sub_text = text
        self._nav_idx = -1  # any external cause of a cue change invalidates the nav chaining hint
        self._hide_preview()  # a new cue → dismiss the last card preview
        if not text.strip():
            self.lines, self.tokens, self.boxes = [], [], []
            self.ov.hide(OverlayId.SUB)
            self.ov.hide(OverlayId.TIP)
            return
        # honour explicit line breaks (\n, ASS \N); tokenize each source line separately
        norm = text.replace("\\N", "\n").replace("\r", "")
        self.lines = [tokenize(ln) for ln in norm.split("\n") if ln.strip()]
        self.tokens = [t for line in self.lines for t in line]
        # score the whole cue (N+1 splits by sentence punctuation across lines); warms lookup cache
        self.styles = self.scorer.score_line(self.tokens) if self.scorer else None
        self._draw_subtitle()

    def _draw_subtitle(self) -> None:
        sr = render_subtitle(
            self.lines,
            self.osd[0],
            size=self.sub_size,
            hover=self.hover if self.hover >= 0 else None,
            styles=self.styles,
        )
        self.boxes = sr.boxes
        ox = (self.osd[0] - sr.image.width) // 2
        oy = self.osd[1] - sr.image.height - self.bottom_margin
        self.sub_origin = (ox, oy)
        if not getattr(self, "_first_sub_logged", False):
            self._first_sub_logged = True
            log.info(
                "first subtitle drawn (%dx%d at %d,%d)", sr.image.width, sr.image.height, ox, oy
            )
        self.ov.show(sr.image, ox, oy, oid=OverlayId.SUB)

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
        tooltip.update_hover(self)

    def set_hover(self, index: int) -> None:
        tooltip.set_hover(self, index)

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
        copy_clipboard("\n".join(miner_ui.sentence_lines(self)))
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

    def _tip_reserve(self) -> int:
        return tooltip.tip_reserve(self)

    def _hit_nested_add(self, x: float, y: float) -> bool:
        return tooltip.hit_nested_add(self, x, y)

    def _hit_nested_speaker(self, x: float, y: float) -> bool:
        return tooltip.hit_nested_speaker(self, x, y)

    def on_click(self) -> None:
        tooltip.on_click(self)

    def _inflected_surface(self, index: int) -> str:
        return tooltip.inflected_surface(self, index)

    def _panel_key(
        self, tok, inflected, mined: bool = False, tabs: bool = True
    ) -> tooltip.PanelKey:
        return tooltip.panel_key(self, tok, inflected, mined, tabs)

    def _is_mined(self, tok) -> bool:
        return tooltip.is_mined(self, tok)

    def _anki_ok(self) -> bool:
        return tooltip.anki_ok(self)

    @staticmethod
    def _darken(rgba, f: float = tooltip.JLPT_DARKEN):
        return tooltip._darken(rgba, f)

    def _jlpt_pill(self, tok) -> Freq | None:
        return tooltip.jlpt_pill(self, tok)

    def _entry_for(self, tok, inflected):
        return tooltip.entry_for_tok(self, tok, inflected)

    def _finish_available(self) -> bool:
        return tooltip.finish_available(self)

    def _panel_for(
        self,
        tok,
        inflected=None,
        min_h: int | None = None,
        finish: bool = False,
        mined: bool | None = None,
        tabs: bool | None = None,
    ):
        return tooltip.panel_for(self, tok, inflected, min_h, finish, mined, tabs)

    def _panel_cache_setdefault(self, key, st) -> TipPanel:
        return tooltip.panel_cache_setdefault(self, key, st)

    # --- background prefetch (warm the paused line's tooltips) — logic in app/prefetch.py --------
    def start_prefetch(self) -> None:
        prefetch.start_prefetch(self)

    def _update_prefetch(self) -> None:
        prefetch.update_prefetch(self)

    def _tip_cap(self) -> int:
        return prefetch.tip_cap(self)

    def _show_tooltip(self, index: int) -> None:
        tooltip.show_tooltip(self, index)

    def _show_tooltip_impl(self, index: int) -> None:
        tooltip.show_tooltip_impl(self, index)

    def _place_panel(
        self, full_w: int, wx: float, wy: float, wh: float, view_h: int
    ) -> tuple[int, int]:
        return tooltip.place_panel(self, full_w, wx, wy, wh, view_h)

    def _refresh_tip_full(self) -> None:
        tooltip.refresh_tip_full(self)

    def _blit_panel(self, bgra, scroll: int, view_h: int, xy, oid: int, header=None):
        return tooltip.blit_panel(self, bgra, scroll, view_h, xy, oid, header)

    # --- per-dictionary tabs + tooltip keys -------------------------------------------------------
    def _update_tabs(self) -> None:
        tooltip.update_tabs(self)

    def _active_section(self) -> int:
        return tooltip.active_section(self)

    def _scroll_to_section(self, offset: int) -> None:
        tooltip.scroll_to_section(self, offset)

    def _tab_step(self, delta: int) -> None:
        tooltip.tab_step(self, delta)

    def _bind_tip_keys(self) -> None:
        """Register the tooltip-scoped keys (idempotent — word switches must not re-bind)."""
        if self._tip_keys_bound:
            return
        for key, msg in TIP_KEYBINDS:
            self.ipc.command("keybind", key, f"script-message {msg}")  # ONE string (the gotcha)
        self._tip_keys_bound = True

    def _unbind_tip_keys(self) -> None:
        """Neutralise the tooltip keys so a leaked bind can't fire ``tab-prev``/etc. when no tooltip is
        up. mpv has no unbind verb over IPC, and ``keybind KEY ""`` is REJECTED — it logs the noisy
        ``[input] Command name missing`` / ``Invalid command for key binding 'LEFT': ''`` triple (visible
        on the Windows console; silently on the mac log). Rebind to the valid no-op ``ignore`` instead:
        no error, and the key stops doing tooltip work while the popup is gone."""
        if not self._tip_keys_bound:
            return
        for key, _msg in TIP_KEYBINDS:
            self.ipc.command("keybind", key, "ignore")  # valid no-op; "" would be rejected by mpv
        self._tip_keys_bound = False

    def _render_tip_view(self) -> None:
        tooltip.render_tip_view(self)

    def _render_nested_view(self) -> None:
        nested_popup.render_nested_view(self)

    def _refresh_nested_full(self) -> None:
        nested_popup.refresh_nested_full(self)

    def _scroll_tip(self, delta: int) -> None:
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
        self._miner.mine_token(self.tokens[idx])

    def _mine_token(self, tok) -> None:
        self._miner.mine_token(tok)

    def _mark_mined(self, expression: str) -> None:
        miner_ui.mark_mined(self, expression)

    # --- card preview (verify correctness / image / sound, one surface) — logic in app/miner_ui.py
    def _preview_mined(self, card, tok, video) -> None:
        miner_ui.preview_mined(self, card, tok, video)

    def _preview_existing(self, note_id: int, card, status: str) -> None:
        miner_ui.preview_existing(self, note_id, card, status)

    def _show_preview(self, pv: PreviewData, audio_path) -> None:
        miner_ui.show_preview(self, pv, audio_path)

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

    def _secondary_text(self) -> str:
        return translation.secondary_text(self)

    def _draw_translation(self) -> None:
        translation.draw_translation(self)

    def _toast(self, text: str, kind: str = "ok", seconds: float = 2.8) -> None:
        img = render_toast(text, kind)
        x = (self.osd[0] - img.width) // 2
        y = round(self.osd[1] * 0.08)
        self.ov.show(img, x, y, oid=OverlayId.TOAST)
        self._toast_until = time.monotonic() + seconds

    def _register_keybinds(self) -> None:
        # mpv `keybind` takes the command as ONE string, e.g. "script-message saitenka-speak".
        # CRITICAL: passing the command as split args silently kills the key — always one string.
        def bind(key: str, msg: str) -> None:
            self.ipc.command("keybind", key, f"script-message {msg}")

        bind(self.translate_key, TRANS_MSG)
        # tooltip: scroll (see monolingual sections below the fold), speak (TTS), copy, click
        bind("WHEEL_UP", SCROLL_UP_MSG)
        bind("WHEEL_DOWN", SCROLL_DOWN_MSG)
        if self._tts_ok:
            bind("a", SPEAK_MSG)  # only bind TTS when a Japanese voice exists (else 'a' is a no-op)
        bind("c", COPY_MSG)  # copy the hovered word
        bind("k", KANJI_MSG)  # open / cycle the hovered word's kanji entry
        bind("Shift+c", COPY_LINE_MSG)  # copy the whole subtitle line
        bind("MBTN_LEFT", CLICK_MSG)  # left = actions (speak / ⊕ mine)
        bind("MBTN_RIGHT", COPY_CLICK_MSG)  # right = copy (word under the cursor) + highlight flash
        if self.anki:
            bind(self.mine_key, MINE_MSG)
            bind(self.mine_all_key, MINE_ALL_MSG)
            bind(self.preview_key, PREVIEW_MSG)
        # subtitle navigation — prev/next/replay sub and sub-delay nudges
        bind(self.sub_prev_key, SUB_PREV_MSG)
        bind(self.sub_next_key, SUB_NEXT_MSG)
        bind(self.sub_replay_key, SUB_REPLAY_MSG)
        bind("z", SUB_DELAY_MINUS_MSG)  # sub-delay −0.1 s (mpv default mapping, kept working)
        bind("Z", SUB_DELAY_PLUS_MSG)  # sub-delay +0.1 s
        bind("x", SUB_DELAY_RESET_MSG)  # reset sub-delay to 0

    def _handle(self, msg: str) -> None:
        if msg == MINE_MSG:
            self.mine_current()
        elif msg == MINE_ALL_MSG:
            self.bulk_mine()
        elif msg == TRANS_MSG:
            self.toggle_translation()
        elif msg == PREVIEW_MSG:
            self.replay_preview()
        elif msg == SCROLL_UP_MSG:
            self._scroll_tip(-round(self.osd[1] * 0.12))
        elif msg == SCROLL_DOWN_MSG:
            self._scroll_tip(round(self.osd[1] * 0.12))
        elif msg == SPEAK_MSG:
            self.speak_hovered()
        elif msg == COPY_MSG:
            self.copy_hovered()
        elif msg == COPY_LINE_MSG:
            self.copy_line()
        elif msg == COPY_CLICK_MSG:
            self.copy_click()
        elif msg == CLICK_MSG:
            self.on_click()
        # subtitle navigation — render the target cue from the index INSTANTLY (if we have one),
        # then issue the real sub-seek so the video catches up behind it (read the position first:
        # _sub_nav samples sub-start/time-pos before the seek moves them).
        elif msg == SUB_PREV_MSG:
            self._sub_nav(-1)
            self.ipc.command("sub-seek", "-1")
        elif msg == SUB_NEXT_MSG:
            self._sub_nav(1)
            self.ipc.command("sub-seek", "1")
        elif msg == SUB_REPLAY_MSG:
            self._sub_nav(0)
            self.ipc.command("sub-seek", "0")
        elif msg == SUB_DELAY_MINUS_MSG:
            self.ipc.command("add", "sub-delay", "-0.1")
        elif msg == SUB_DELAY_PLUS_MSG:
            self.ipc.command("add", "sub-delay", "0.1")
        elif msg == KANJI_MSG:
            self.kanji_current()
        elif msg == TAB_PREV_MSG:
            self._tab_step(-1)
        elif msg == TAB_NEXT_MSG:
            self._tab_step(1)
        elif msg == TIP_UP_MSG:
            self._scroll_tip(-round(self.osd[1] * 0.12))
        elif msg == TIP_DOWN_MSG:
            self._scroll_tip(round(self.osd[1] * 0.12))
        elif msg == TIP_CLOSE_MSG:
            self.set_hover(-1)
        elif msg == SUB_DELAY_RESET_MSG:
            self.ipc.command("set_property", "sub-delay", "0")

    # --- run loop -----------------------------------------------------------------------------
    def poll_once(self) -> bool:
        """One tick: sync subtitle + hover, handle key events. False if mpv went away."""
        try:
            self.ipc.pump()  # sole socket reader in steady state: fetch events, detect mpv quit
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
            if scroll_steps:
                self._scroll_tip(scroll_steps * round(self.osd[1] * 0.14))
            if self._toast_until and time.monotonic() > self._toast_until:
                self.ov.hide(OverlayId.TOAST)
                self._toast_until = 0.0
            if self._flash_until and time.monotonic() >= self._flash_until:
                oid, self._flash_oid, self._flash_until = self._flash_oid, None, 0.0
                if oid == OverlayId.NESTED:
                    self._render_nested_view()  # redraw without the highlight border
                elif oid == OverlayId.TIP:
                    self._render_tip_view()
            if self.refresh_osd() and self.tokens:
                self._draw_subtitle()
            self._reconcile_sub_text(self._prop("sub-text") or "")
            self._maybe_log_stall()
            # progressive startup: inject background-loaded deps (once), else animate the spinner
            if self._pending_deps is not None:
                deps, self._pending_deps = self._pending_deps, None
                self._apply_deps(deps)
            elif self._loading:
                self._draw_loading()
            self._update_hover()
            if self._tip_dirty:  # a worker finished the shown panel's deferred tail
                self._tip_dirty = False
                self._refresh_tip_full()
            if self._nest.dirty:  # …or the nested scan popup's tail
                self._nest.dirty = False
                self._refresh_nested_full()
            self._update_prefetch()
            if self._translation_visible() and self._secondary_text() != self._trans_text:
                self._draw_translation()  # keep the (manual or auto) translation current as subs change
            return True
        except (OSError, ValueError):
            return False

    def _maybe_log_stall(self) -> None:
        """One-time loud diagnostic for 'mpv plays but nothing draws': if several seconds pass with no
        subtitle text ever observed, mpv's IPC replies/events aren't reaching us. The byte count from
        the reader thread distinguishes the causes — 0 bytes means the pipe read direction is dead (the
        classic Windows named-pipe failure); >0 bytes means reads work but the subtitle track/property
        never produced text. Playback continues regardless, so this only lives in overlay.log / report."""
        if getattr(self, "_stall_warned", False) or self.sub_text:
            return
        started = getattr(self, "_run_started", None)
        if started is None or time.monotonic() - started < 4.0:
            return
        self._stall_warned = True
        log.warning(
            "no subtitle text %.0fs after start (bytes from mpv=%d). Nothing drawing usually means "
            "mpv's IPC replies aren't reaching the overlay (bytes=0 → dead pipe read) or no JP "
            "subtitle track/text was selected (bytes>0).",
            time.monotonic() - started,
            getattr(self.ipc, "_bytes_read", -1),
        )

    def _seed_mined(self) -> None:
        self._miner.seed_mined()

    # --- subtitle navigation (instant render, then seek) --------------------------------------
    def load_sub_index(self, path) -> None:
        """Parse the external subtitle file at ``path`` into a cue index so Alt+←/→/↓ can render the
        target line instantly. Fail-soft: an unreadable/empty/unsupported file just leaves the index
        None → navigation falls back to a plain mpv sub-seek."""
        self._sub_index = load_index(path)

    def _get_float(self, prop: str) -> float | None:
        v = self._get(prop)  # a direct get_property is fine: nav keys are rare, not per-tick
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _sub_nav(self, delta: int) -> bool:
        """Render the cue ``delta`` steps away (-1 prev / 0 replay / +1 next) in the overlay right
        now, from the parsed index — the perceived-instant half of subtitle navigation. Returns True
        if it drew a target line. The caller still issues the real ``sub-seek`` so the video catches
        up; the poll loop reconciles to mpv's ``sub-text`` once the seek settles.

        Chaining works while the video seek is still in flight (time-pos/sub-start are stale): after
        a nav render ``sub_text`` is the line we drew, so ``locate`` finds it by text and ``_nav_idx``
        disambiguates duplicates — next/next/next steps forward predictably."""
        idx = self._sub_index
        if idx is None or len(idx) == 0:
            return False
        with otel_metrics.instrumented(otel_metrics.sub_seek_duration_ms, "sub_seek"):
            sub_start = self._get_float("sub-start")
            time_pos = self._get_float("time-pos")
            current = idx.locate(
                text=self.sub_text, sub_start=sub_start, time_pos=time_pos, preferred=self._nav_idx
            )
            if current < 0:
                return False
            # Is a cue actually on screen now, or is `current` just the upcoming one in a gap? A sub
            # is showing (non-empty text), or the position falls inside current's span. This decides
            # whether prev/next straddle the cue or step onto the upcoming one (see SubIndex.target).
            c = idx.cues[current]
            inside = bool(self.sub_text.strip())
            if not inside and sub_start is not None:
                inside = c.start <= sub_start < c.end
            if not inside and time_pos is not None:
                inside = c.start <= time_pos < c.end
            tgt = idx.target(current, delta, inside=inside)
        if tgt < 0:
            return False  # out of range / ambiguous → let mpv's sub-seek handle it
        self.set_subtitle(idx.cues[tgt].text)  # instant overlay render (also resets _nav_idx)
        self._nav_idx = tgt
        # Guard the reconcile: mpv's sub-text briefly reads empty mid-seek; ignoring that avoids a
        # blank flicker before it settles on the real (matching) cue text. ~1s covers a slow seek.
        self._sub_settle_until = time.monotonic() + 1.0
        return True

    def _reconcile_sub_text(self, text: str) -> None:
        """Poll-loop hook: adopt mpv's current ``sub-text`` when it changed. mpv is the source of
        truth (it corrects the line if our instant-nav index guessed wrong), EXCEPT for the empty
        blip mpv emits mid-seek right after a manual sub-nav — swallow that within the settle window
        so the overlay doesn't flash blank before the real cue text lands."""
        if text == self.sub_text:
            return
        if text.strip() or time.monotonic() >= self._sub_settle_until:
            self.set_subtitle(text)
            self._sub_settle_until = 0.0

    # --- progressive dep loading --------------------------------------------------------------
    def load_deps_async(self, cfg: dict, build=None) -> None:
        """Load coloring/dict/mining collaborators on a BACKGROUND thread (dicts/scorer/anki — none
        touch the mpv IPC), then hand them to the poll loop, which injects them on the main thread.
        Plain subs draw meanwhile; a spinner shows until the deps land.

        ``build`` is a zero-arg callable returning ``(scorer, anki, mine_cfg, dict_set)``; it defaults
        to ``build_reader_deps(cfg)`` (attach/plugin mode). ``run`` passes its own closure so it can
        honour CLI flags (``--dict/--freq/--anki-decks/--mine`` …) while still loading progressively.
        The one rule: the builder must NOT touch the mpv IPC (it runs off the main thread)."""
        self._loading = True

        if build is None:

            def _default_build():
                from overlay.app.reader_deps import build_reader_deps

                return build_reader_deps(cfg)

            build = _default_build

        def _load() -> None:
            try:
                with otel_metrics.traced("load_deps_async"):
                    scorer, anki, mine_cfg, dict_set = build()
                self._pending_deps = {
                    "scorer": scorer,
                    "anki": anki,
                    "mine_cfg": mine_cfg,
                    "dict_set": dict_set,
                }
            except Exception:
                log.warning("background dep load failed — staying subs-only", exc_info=True)
                self._pending_deps = {}  # signal "done" so the spinner stops

        threading.Thread(target=_load, name="saitenka-deps", daemon=True).start()

    def _apply_deps(self, deps: dict) -> None:
        """Inject loaded deps on the main thread and light up coloring/tooltips/mining in place."""
        self._loading = False
        self.ov.hide(OverlayId.LOADING)
        self.scorer = deps.get("scorer")
        self.anki = deps.get("anki")
        self.mine_cfg = deps.get("mine_cfg")
        self.dict_set = deps.get("dict_set")
        if self.sub_text:  # re-tokenise + re-score the CURRENT cue so coloring appears now
            self.set_subtitle(self.sub_text)
        if self.anki:
            self._seed_mined()  # ⊕→✓ from past mining
        self.start_prefetch()  # spin up prefetch now that dict_set exists (no-op if still None)

    def _draw_loading(self) -> None:
        """Draw the throttled top-left spinner while deps load (main thread, from the poll loop)."""
        now = time.monotonic()
        if now < self._load_next:
            return
        self._load_next = now + 0.08
        from overlay.app.loading import loading_image

        img = loading_image("saitenka loading dictionaries", self._load_frame)
        self._load_frame += 1
        try:
            self.ov.show(img, x=24, y=24, oid=OverlayId.LOADING)
        except Exception:
            log.debug("loading spinner draw failed", exc_info=True)

    def run(self, interval: float | None = None) -> None:
        interval = interval if interval is not None else self.poll_interval
        self.refresh_osd()
        self.start_observing()  # event-driven property reads from here on
        self._setup_secondary()
        self._register_keybinds()
        self._seed_mined()
        self.start_prefetch()
        mode = "free-threaded (GIL off)" if gil_disabled() else "GIL"
        print(f"[saitenka] runtime: {mode} · {len(self._prefetch_threads)} prefetch worker(s)")
        self._run_started = time.monotonic()  # baseline for the no-subtitle stall diagnostic
        self._stall_warned = False
        while self.poll_once():
            time.sleep(interval)

    def close(self) -> None:
        import shutil

        self._stop.set()  # signal the workers; they do no IPC so this is race-free
        for th in self._prefetch_threads:
            th.join(timeout=2.0)  # daemon threads → process can exit even if one is stuck
        self.ov.close()
        shutil.rmtree(self._tmp, ignore_errors=True)  # clean up the per-session scratch dir
