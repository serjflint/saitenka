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
from typing import NamedTuple

import numpy as np

from overlay.app.card_preview import PreviewData
from overlay.app.config import ReaderOptions
from overlay.app import miner_ui, nested_popup, prefetch
from overlay.app.miner import Miner, tag_slug
from overlay.app.nested_popup import TIP_GAP
from overlay import otel_metrics
from overlay.app.overlay_ids import OverlayId
from overlay.app.perf import gil_disabled, timed
from overlay.app.popups import PopupView, TipPanel
from overlay.app.prefetch import FinishItem
from overlay.app.lookup import card_for, entry_for
from overlay.app.sub_index import SubIndex, load_index
from overlay.app.media import (
    copy_clipboard,
    speak,
    tts_available,
)
from overlay.app.subtitles import render_subtitle
from overlay.app.toast import render_toast
from overlay.app.tokenize import SKIP_POS, Token, tokenize
from overlay.model import Span, Style
from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.osd import Overlay
from overlay.mpvio.osd import to_bgra_array
from overlay.panel import (
    Freq,
    LazyPanel,
    header_add_rect,
    header_speaker_rect,
    panel_rows,
    render_tab_row,
    tab_strip_height,
)
from overlay.render.flow import render_flow
from overlay.render.layout import Block, inline_width

log = logging.getLogger(__name__)

# The nested popup gets its own (roomier) height cap (TooltipOptions.nested_max_frac) so shrinking
# the base tooltip (tip_max_frac) doesn't cramp the deep-dive; the nested popup also carries no
# dict-tab strip / reserve (space-saving).
AUX_POS = {"助動詞"}  # trailing tokens glued to the verb/adj surface for the inflection chain
_HIT_TEST_SAMPLE_EVERY = 8  # OTel hit-test histogram samples 1-in-N poll ticks (unlike perf.timed
# above, which is an unconditional deque append and stays on every tick)
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


class PanelKey(NamedTuple):
    """Identity of a rendered tooltip panel — the ``_panel_cache`` key. Named (not a bare tuple) so
    callers read ``.mined`` / ``.anki_ok`` instead of brittle positions, and adding a field can't
    silently shift another. Still a tuple, so hashing, dict-key use, and equality with a plain tuple
    of the same values are all unchanged."""

    lemma: str
    surface: str
    reading: str
    inflected: str
    width: int
    anki_ok: bool  # is Anki reachable now → is the ⊕ button drawn (rechecked per show, ~3s TTL)
    mined: bool  # is the word already in the deck → its ⊕ shows ✓ (tests read this by name)
    tabs: bool = (
        True  # dict-tab strip reserved/drawn (base tooltip); a nested popup builds with tabs=False
    )


# Properties the poll loop consumes event-driven (observe_property) instead of issuing 3–5
# blocking get_property round-trips per 25 ms tick. One initial read seeds pre-observe state.
OBSERVED_PROPS = ("sub-text", "mouse-pos", "osd-dimensions", "pause", "secondary-sub-text")
EN_LANGS = {"en", "eng", "en-us", "en-gb", "eng-us", "english"}


FLASH_BGRA = (90, 214, 255, 255)  # premultiplied BGRA of the warm highlight (RGB 255,214,90)
JLPT_DARKEN = (
    0.62  # darken the pastel underline hue for the pill name-segment so white text is legible
)


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
        self._tip_xy = (0, 0)
        self._tip_state: TipPanel | None = (
            None  # _TipPanel currently shown (viewport-first render may still be filling)
        )
        self._tip_key: PanelKey | None = (
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
        """Hover with hysteresis across the popup stack: keep each level alive while the cursor is on
        its trigger OR on the popup itself, lingering ``hide_delay`` after leaving both. Hovering a
        word *inside* the tooltip opens a nested scan popup."""
        with timed("hover_hit_test"):
            # Sampled, not every tick: this runs at poll cadence (~40Hz), and an OTel histogram
            # .record() call costs real cycles unlike perf.timed's plain deque append above.
            self._hit_test_tick = (self._hit_test_tick + 1) % _HIT_TEST_SAMPLE_EVERY
            if otel_metrics.hit_test_duration_ms is not None and self._hit_test_tick == 0:
                # instrumented() (span + histogram) only on the sampled tick — a span every tick
                # would flood the trace at poll cadence for no visualization benefit.
                with otel_metrics.instrumented(otel_metrics.hit_test_duration_ms, "hit_test"):
                    self._update_hover_impl()
            else:
                self._update_hover_impl()

    def _update_hover_impl(self) -> None:
        mp = self._prop("mouse-pos") or {}
        inside = bool(mp.get("hover"))
        self._mouse_in = inside  # engagement signal for prefetch
        mx, my = mp.get("x", -1), mp.get("y", -1)
        self._last_mouse = (mx, my)
        over_word = self._hit(mx, my) if (inside and self.tokens) else -1
        over_tip = inside and self._tip_rect is not None and self._in_rect(self._tip_rect, mx, my)
        over_nest = (
            inside and self._nest.rect is not None and self._in_rect(self._nest.rect, mx, my)
        )

        # --- nested level: scan a word inside the tooltip; keep its popup alive while engaged ---
        # A cross-reference LINK is click-to-open, NOT hover-scan — so scrolling past / reading a
        # link doesn't spawn scan popups that clutter the panel.
        scan = self._scan_hit(mx, my) if (over_tip and not over_nest) else None
        if scan is not None and self._link_hit(
            mx, my, self._tip_state, self._tip_xy, self._tip_scroll
        ):
            scan = None
        if scan is not None:
            now = time.monotonic()
            if scan.text != self._scan_target:
                self._scan_target, self._scan_since = scan.text, now  # moved → restart the dwell
            # open only once the cursor has rested on this cell (scan delay), and it isn't already shown
            if now - self._scan_since >= self.scan_delay and self._nest.tail != scan.text:
                self._show_nested(scan)
            self._nest.hide_at = 0.0
        elif over_nest:
            self._scan_target = None
            self._nest.hide_at = 0.0
        elif self._nest.state is not None:
            self._scan_target = None
            now = time.monotonic()
            if self._nest.hide_at == 0.0:
                self._nest.hide_at = now + self.hide_delay
            elif now >= self._nest.hide_at:
                self._hide_nested()
        else:
            self._scan_target = None

        # --- base tooltip: also kept alive while the cursor is on the nested popup ---
        if over_word >= 0:
            # First open is instant, but SWITCHING to a different word needs a brief dwell — so dragging
            # the cursor up to the tooltip across the OTHER line of a two-line sub doesn't hijack it onto
            # every word it passes over. Only resting on a new word switches.
            if over_word == self.hover:
                self._word_target = None
            else:
                now = time.monotonic()
                if over_word != self._word_target:
                    self._word_target, self._word_since = over_word, now
                if self.hover < 0 or now - self._word_since >= self.hover_switch_delay:
                    self.set_hover(over_word)
                    self._word_target = None
            self._hide_at = 0.0
        elif over_tip or over_nest:
            self._hide_at = 0.0  # resting on the tooltip or its scan popup → keep it alive
            self._word_target = None
        elif self.hover != -1:
            self._word_target = None
            now = time.monotonic()
            if self._hide_at == 0.0:
                self._hide_at = now + self.hide_delay
            elif now >= self._hide_at:
                self.set_hover(-1)
                self._hide_at = 0.0

    def set_hover(self, index: int) -> None:
        if index == self.hover:
            return
        self.hover = index
        self._draw_subtitle()
        if index < 0:
            self._teardown_tip()  # hide OverlayId.TIP/OverlayId.NESTED, reset all state, release pause
            return
        self._show_tooltip(index)
        self._sync_auto_translation()  # hovering a word → auto-reveal the translation

    def speak_hovered(self) -> None:
        # speak the DICTIONARY-form reading (習う → ならう), not the kanji surface (say reads 習 as
        # しゅう → "shuuwa") nor the bare stem reading ならわ. Falls back to the token reading/surface.
        if 0 <= self.hover < len(self.tokens):
            t = self.tokens[self.hover]
            speak(self._hover_reading or t.reading or t.surface)

    def copy_hovered(self) -> None:
        if 0 <= self.hover < len(self.tokens):
            self._copy_token(self.tokens[self.hover])

    @staticmethod
    def _token_clip(t) -> str:
        return f"{t.surface}【{t.reading}】" if t.reading else t.surface

    def _copy_token(self, t) -> None:
        copy_clipboard(self._token_clip(t))
        self._toast(f"copied {t.surface}", "ok", 1.2)

    def copy_line(self) -> None:
        """Shift+C — copy the whole subtitle cue under the cursor (all its lines)."""
        if not self.lines:
            self._toast("no line to copy", "warn", 1.2)
            return
        copy_clipboard("\n".join(miner_ui.sentence_lines(self)))
        self._toast("copied line", "ok", 1.2)

    def _flash(self, oid: int) -> None:
        """Pulse a "copied" highlight border on a popup as copy feedback, then let the poll loop
        restore it after ``flash_secs``."""
        self._flash_oid = oid
        self._flash_until = time.monotonic() + self.flash_secs
        self._render_nested_view() if oid == OverlayId.NESTED else self._render_tip_view()

    def copy_click(self) -> None:
        """Right-click — copy the word under the cursor (the inner scanned word if over the nested
        popup, else the hovered/pointed subtitle word), with a brief highlight flash."""
        mp = self._get("mouse-pos") or {}
        x, y = mp.get("x", -1), mp.get("y", -1)
        if self._nest.rect is not None and self._in_rect(self._nest.rect, x, y):
            if self._nest.token is not None:
                self._copy_token(self._nest.token)
                self._flash(OverlayId.NESTED)
            return
        if self._tip_rect is not None and self._in_rect(self._tip_rect, x, y):
            self.copy_hovered()
            self._flash(OverlayId.TIP)
            return
        idx = self._hit(x, y) if self.tokens else -1  # not over a popup → the subtitle word, if any
        if idx >= 0:
            self._copy_token(self.tokens[idx])

    def _hit_header_region(self, x: float, y: float, prect, xy, scroll: int, view_h: int) -> bool:
        """Is (x, y) on a header button (panel-space ``prect``)? Only while it's inside the scrolled
        viewport (the header scrolls off). Shared by the base tooltip and the nested popup."""
        px, py, pw, ph = prect
        top = py - scroll
        if top < 0 or top + ph > view_h:  # header scrolled out of the viewport
            return False
        sx, sy = xy
        return self._in_rect((sx + px, sy + top, pw, ph), x, y)

    def _tip_reserve(self) -> int:
        """The base tooltip's tab-strip top-reserve (0 when no tabs) — header hit-boxes must match it."""
        return self._tip_state.lazy.top_reserve if self._tip_state is not None else 0

    def _hit_header_add(self, x: float, y: float) -> bool:
        if self._tip_state is None or not self._anki_ok():  # ⊕ only when Anki is reachable now
            return False
        return self._hit_header_region(
            x,
            y,
            header_add_rect(
                self.tip_width, top_reserve=self._tip_reserve(), speak_button=self._tts_ok
            ),
            self._tip_xy,
            self._tip_scroll,
            self._tip_view_h,
        )

    def _hit_header_speaker(self, x: float, y: float) -> bool:
        if self._tip_state is None or not self._tts_ok:  # 🔊 hidden when no JA TTS voice
            return False
        return self._hit_header_region(
            x,
            y,
            header_speaker_rect(self.tip_width, top_reserve=self._tip_reserve()),
            self._tip_xy,
            self._tip_scroll,
            self._tip_view_h,
        )

    def _hit_nested_add(self, x: float, y: float) -> bool:
        if self._nest.state is None or not self._anki_ok():
            return False
        return self._hit_header_region(
            x,
            y,
            header_add_rect(self.tip_width, speak_button=self._tts_ok),
            self._nest.xy,
            self._nest.scroll,
            self._nest.view_h,
        )

    def _hit_nested_speaker(self, x: float, y: float) -> bool:
        if self._nest.state is None or not self._tts_ok:  # 🔊 hidden when no JA TTS voice
            return False
        return self._hit_header_region(
            x,
            y,
            header_speaker_rect(self.tip_width),
            self._nest.xy,
            self._nest.scroll,
            self._nest.view_h,
        )

    def on_click(self) -> None:
        # Left-click drives buttons only — the card preview's ✕/screenshot/▶, and each popup's ⊕/🔊.
        # Clicking an empty area does NOTHING: audio must not fire on a stray body click.
        mp = self._get("mouse-pos") or {}
        x, y = mp.get("x", -1), mp.get("y", -1)
        if self._click_preview(x, y):
            return
        # The nested popup sits on top → test it first.
        if self._nest.rect is not None and self._in_rect(self._nest.rect, x, y):
            if self._hit_nested_add(x, y) and self._nest.token is not None:
                self._mine_token(self._nest.token)  # ⊕ → mine the *inner* (scanned) word
            elif self._hit_nested_speaker(x, y) and self._nest.state:
                speak(self._nest.state.reading)  # 🔊 → read the inner word aloud
            else:
                lb = self._link_hit(x, y, self._nest.state, self._nest.xy, self._nest.scroll)
                if lb is not None:
                    self._open_link(lb, self._nest.xy, self._nest.scroll)  # cross-ref → navigate
            return
        if self._tip_rect is not None and self._in_rect(self._tip_rect, x, y):
            # Header ⊕/🔊 are specific top-right affordances — they win over the general dict-tab
            # band, which spans the full width and would otherwise steal a click that overlaps them.
            if self._hit_header_add(x, y):
                self.mine_current()  # ⊕ → mine the hovered word into Anki
                return
            if self._hit_header_speaker(x, y):
                self.speak_hovered()  # 🔊 → hear the word (TTS)
                return
            for rect, off in zip(self._tab_rects, self._tab_offsets, strict=False):
                if self._in_rect(rect, x, y):  # dict tab → jump the viewport to that section
                    self._scroll_to_section(off)
                    return
            lb = self._link_hit(x, y, self._tip_state, self._tip_xy, self._tip_scroll)
            if lb is not None:
                self._open_link(lb, self._tip_xy, self._tip_scroll)  # cross-ref → nested popup
            else:
                self._click_kanji_fallback(x, y)  # single-ideograph cell → kanji entry

    def _inflected_surface(self, index: int) -> str:
        """Token surface + trailing auxiliary tokens (助動詞), so the chain deinflects the full word
        (習わ + ぬ → 習わぬ); the tokenizer splits inflected verbs from their auxiliaries."""
        s = self.tokens[index].surface
        j = index + 1
        while j < len(self.tokens) and self.tokens[j].pos in AUX_POS:
            s += self.tokens[j].surface
            j += 1
        return s

    def _panel_key(self, tok, inflected, mined: bool = False, tabs: bool = True) -> PanelKey:
        # anki_ok is live (rebuilds the cached panel when Anki opens/closes; stable within its ~3s TTL).
        # ``tabs`` distinguishes the base build (with the dict-tab reserve) from a nested build (none),
        # so the same word shown in both places doesn't share the wrong reserve.
        return PanelKey(
            tok.lemma,
            tok.surface,
            tok.reading,
            inflected,
            self.tip_width,
            self._anki_ok(),
            mined,
            tabs,
        )

    def _is_mined(self, tok) -> bool:
        """Is this token's word already in the deck? (its ⊕ shows ✓ instead). Cheap short-circuit
        while nothing has been mined; else a card_for lookup (lru-cached)."""
        if not self._mined:
            return False
        try:
            return card_for(tok).expression in self._mined
        except Exception:
            return False

    def _anki_ok(self) -> bool:
        """Is AnkiConnect reachable RIGHT NOW? Gates the ⊕ button per card show, so it appears/hides as
        the user opens/closes Anki mid-session (not frozen at startup). Kept fast: a short timeout with
        0 retries fails immediately when Anki is closed, and the result is cached ``anki_ok_ttl``
        seconds so rapid hovers don't ping repeatedly. False when mining isn't configured at all."""
        if self.anki is None:
            return False
        now = time.monotonic()
        ts, ok = self._anki_cache
        if now - ts < self.anki_ok_ttl:
            return ok
        from overlay.app.anki import anki_reachable

        ok = anki_reachable(
            timeout=self.anki_ping_timeout
        )  # resolves host/key from config; 0 retries
        self._anki_cache = (now, ok)
        return ok

    @staticmethod
    def _darken(rgba, f: float = JLPT_DARKEN):
        r, g, b, a = rgba
        return (round(r * f), round(g * f), round(b * f), a)

    def _jlpt_pill(self, tok) -> Freq | None:
        """A ``JLPT | Nx`` pill for the tooltip's frequency row, shown only when the word has a JLPT
        level — the same signal the subtitle draws as an underline (``Scorer._style``). The pill's hue
        is the level's underline color (darkened for legible white text), so the tooltip and the
        underline read as the same thing."""
        from overlay.app.scoring import _is_content

        sc = self.scorer
        if sc is None or not getattr(sc, "enable_jlpt", False) or sc.jlpt is None:
            return None
        # Gate on content POS exactly like the subtitle underline (Scorer._style). Without this a
        # particle/aux (は, ね) whose bare-kana READING collides with an N1 word's reading in the JLPT
        # map gets mislabelled — usually N1, since _put keeps the highest level. Better no pill.
        if not _is_content(tok):
            return None
        level = sc.jlpt.level(tok.lemma, tok.surface, tok.reading)
        if not level:
            return None
        base = sc.palette.jlpt.get(level, (96, 125, 175, 255))
        return Freq("JLPT", level, self._darken(base))

    def _entry_for(self, tok, inflected):
        """Look up the panel entry and fold in the JLPT pill (near the frequency pills) when the word
        carries a JLPT level, so it mirrors the subtitle underline.

        Never mutates the lru_cached Entry from lookup.lookup_entry / dict_set.entry_for — returns
        a shallow copy with a new freqs list so repeated calls do not accumulate pills."""
        import dataclasses as _dc

        entry = self.dict_set.entry_for(tok, inflected) if self.dict_set else entry_for(tok)
        pill = self._jlpt_pill(tok)
        if pill is not None and hasattr(entry, "freqs"):
            # Build the pill list into a shallow copy — never mutate the cached original.
            entry = _dc.replace(entry, freqs=[pill, *entry.freqs])
        return entry

    def _finish_available(self) -> bool:
        """A running prefetch worker can render a tooltip's deferred tail. Without one (prefetch off,
        or before the workers start) we finish synchronously so a partial panel never gets stuck."""
        return bool(self.prefetch and self._prefetch_threads)

    def _panel_for(
        self,
        tok,
        inflected=None,
        min_h: int | None = None,
        finish: bool = False,
        mined: bool | None = None,
        tabs: bool | None = None,
    ):
        """The memoised :class:`_TipPanel` for a token. ``finish`` renders the whole entry (prefetch /
        no-worker path); otherwise only the head that fills ``min_h`` px is rendered now (viewport-first)
        and the tail is deferred. Re-hovering is instant and scrolling is cheap. ``mined`` (default: look
        it up) selects the ⊕ vs ✓ header variant and is part of the cache key. ``tabs`` (default: the
        ``show_dict_tabs`` config) reserves + will draw the sticky dict-tab strip; a nested popup passes
        ``tabs=False`` so it carries no strip and no reserved band.

        Thread-safe: the panel is *built* lock-free (thread-local DB conns + fonts, each render owns its
        images), and only the tiny cache write/LRU update is locked.  On a free-threaded (no-GIL) build,
        OrderedDict.get() is NOT atomic, so cache hits also acquire the lock briefly to move_to_end.
        Hovers remain snappy because the lock is held for only a few microseconds (no rendering inside)."""
        if mined is None:
            mined = self._is_mined(tok)
        if tabs is None:
            tabs = self.show_dict_tabs
        key = self._panel_key(tok, inflected, mined, tabs)
        st = self._panel_cache.get(key)
        if st is None:
            if otel_metrics.panel_cache_misses is not None:
                otel_metrics.panel_cache_misses.add(1)
            with otel_metrics.instrumented(otel_metrics.render_duration_ms, "render"):
                entry = self._entry_for(tok, inflected)
                # Reserve space for the sticky dict-tab strip (base tooltip, ≥2 dicts, tabs on) so it
                # clears the header (reading + ⊕/🔊) instead of overlapping it. Use the WRAPPED height
                # for this word's dict names at this width, so a many-dict strip that wraps onto
                # several rows reserves enough. Nested popups (tabs=False) reserve nothing.
                reserve = (
                    tab_strip_height([d.dict_name for d in entry.defs], self.tip_width)
                    if (tabs and len(entry.defs) >= 2)
                    else 0
                )
                lazy = LazyPanel(
                    panel_rows(
                        entry,
                        self.tip_width,
                        add_button=self._anki_ok(),
                        mined=mined,
                        speak_button=self._tts_ok,
                    ),
                    self.tip_width,
                    top_reserve=reserve,
                )
                st = _TipPanel(lazy, getattr(entry, "reading", "") or tok.reading)
            with self._cache_lock:
                st = self._panel_cache_setdefault(key, st)
        else:
            if otel_metrics.panel_cache_hits is not None:
                otel_metrics.panel_cache_hits.add(1)
            # Cache hit: move to end (most-recently-used) under the lock so the LRU order stays accurate.
            with self._cache_lock:
                try:
                    self._panel_cache.move_to_end(key)
                except KeyError:
                    pass  # evicted between get() and move_to_end() — harmless
        if finish:
            st.finish()
        else:
            st.render_head(min_h if min_h is not None else self._tip_cap())
        return st

    def _panel_cache_setdefault(self, key, st) -> _TipPanel:
        """Insert ``st`` for ``key`` if not already present; evict the LRU entry when over the cap.
        Must be called under ``self._cache_lock``.  First-writer-wins: if two workers race to build
        the same panel, the winner's result is kept and the loser is discarded (both are equivalent)."""
        if key in self._panel_cache:
            self._panel_cache.move_to_end(key)
            return self._panel_cache[key]
        # Evict least-recently-used entries until we are at the limit.
        while len(self._panel_cache) >= self.panel_cache_max:
            self._panel_cache.popitem(last=False)  # FIFO/LRU: oldest (first) entry out
        self._panel_cache[key] = st
        return st

    # --- background prefetch (warm the paused line's tooltips) — logic in app/prefetch.py --------
    def start_prefetch(self) -> None:
        prefetch.start_prefetch(self)

    def _update_prefetch(self) -> None:
        prefetch.update_prefetch(self)

    def _tip_cap(self) -> int:
        return prefetch.tip_cap(self)

    def _show_tooltip(self, index: int) -> None:
        with timed("show_tooltip"):
            self._show_tooltip_impl(index)

    def _show_tooltip_impl(self, index: int) -> None:
        self._hide_nested()  # switching the base word drops any stale scan popup
        self._kanji_index = 0  # a new word restarts the `k` kanji cycle
        tok = self.tokens[index]
        inflected = self._inflected_surface(index)
        cap = self._tip_cap()
        # Viewport-first: paint only the head that fills the viewport now; a worker renders the tail.
        # Without a worker (prefetch off) finish synchronously so the panel is never left partial.
        mined = self._is_mined(tok)
        key = self._panel_key(tok, inflected, mined)
        st = self._panel_for(
            tok, inflected, min_h=cap, finish=not self._finish_available(), mined=mined
        )
        self._tip_state, self._tip_key, self._tip_dirty = st, key, False
        self._tip_bgra = st.bgra()  # decompress the cached panel into the active scroll buffer
        self._hover_reading = st.reading
        self._tip_scroll = 0

        ox, oy = self.sub_origin
        b = self.boxes[index]
        wx, wy = ox + b.x, oy + b.y
        # Safe area: keep clear of the OSC/window header at the top and the controls/edge at the
        # bottom, so the tooltip never spills under the window chrome. It scrolls, so we cap the
        # height rather than trying to fit the whole (very tall) entry.
        assert st.ready  # head render above guarantees the panel is stored
        ph, pw = st.shape[0], st.shape[1]
        self._tip_view_h = min(ph, cap)
        self._tip_xy = self._place_panel(pw, wx, wy, b.h, self._tip_view_h)
        self._update_tabs()
        self._render_tip_view()
        self._bind_tip_keys()  # LEFT/RIGHT/UP/DOWN/ESC live only while the tip shows
        if not st.complete:
            self._finish_q.put(FinishItem(st, key))  # worker fills the tail → _tip_dirty → refresh
        if self.pause_on_tooltip and not self._paused_by_tip and not self._prop("pause"):
            self.ipc.command("set_property", "pause", True)  # freeze the frame while you read
            self._paused_by_tip = True

    def _place_panel(
        self, full_w: int, wx: float, wy: float, wh: float, view_h: int
    ) -> tuple[int, int]:
        """Choose a top-left (tx, ty) for a panel of width ``full_w`` and height ``view_h`` anchored to
        an on-screen word box (wx, wy, wh): above it if there's room, else below, clamped to the safe
        area. Shared by the base tooltip and nested popups."""
        margin = max(16, round(self.osd[1] * 0.05))
        above_room = wy - TIP_GAP - margin
        below_room = (self.osd[1] - margin) - (wy + wh + TIP_GAP)
        if above_room >= view_h or above_room >= below_room:
            ty = wy - TIP_GAP - view_h  # above the word
        else:
            ty = wy + wh + TIP_GAP  # below the word
        tx = max(margin, min(wx, self.osd[0] - full_w - margin))
        ty = max(margin, min(ty, self.osd[1] - margin - view_h))
        return int(tx), int(ty)

    def _refresh_tip_full(self) -> None:
        """A background finish grew the shown panel (deferred bodies rendered) → re-upload the view so
        the scrollbar reflects the true height and the below-the-fold content is scrollable."""
        st = self._tip_state
        if st is None or not st.ready:
            return
        self._tip_bgra = st.bgra()  # re-decompress the grown panel into the active scroll buffer
        self._update_tabs()  # the streamed tail may add sections / move offsets
        self._render_tip_view()

    def _blit_panel(self, bgra, scroll: int, view_h: int, xy, oid: int, header=None):
        """Upload a scrolled viewport slice of a premultiplied BGRA panel to an OSD overlay, drawing a
        scrollbar thumb when the panel is taller than the viewport. ``header`` is an opaque BGRA
        strip composited over the TOP of the viewport — the sticky dict-tab row.
        Returns the shown screen rect."""
        full_h, full_w = bgra.shape[:2]
        vh = min(view_h, full_h)
        y0 = max(0, min(scroll, max(0, full_h - vh)))
        view = bgra[y0 : y0 + vh].copy()  # cheap slice of the pre-converted panel
        if header is not None and header.shape[0] < view.shape[0]:
            view[: header.shape[0], : header.shape[1]] = header  # opaque → occludes scrolled rows
        if full_h > vh:  # scrollbar thumb (premultiplied BGRA gray)
            track = vh - 8
            th = max(28, int(track * vh / full_h))
            tyb = 4 + int((track - th) * (y0 / max(1, full_h - vh)))
            view[tyb : tyb + th, full_w - 7 : full_w - 3] = (99, 99, 99, 210)
        if self._flash_oid == oid and time.monotonic() < self._flash_until:
            b = 4  # "copied" highlight border (a brief visual pulse)
            view[:b, :] = view[-b:, :] = FLASH_BGRA
            view[:, :b] = view[:, -b:] = FLASH_BGRA
        tx, ty = xy
        self.ov.show_bgra(view, tx, ty, oid=oid)
        return (tx, ty, full_w, view.shape[0])

    # --- per-dictionary tabs + tooltip keys -------------------------------------------------------
    def _update_tabs(self) -> None:
        """Recompute the dict-tab sections from the shown panel (≥2 sections → tabs). Off entirely when
        ``show_dict_tabs`` is disabled — the panel is then built without the reserve too, so drawing a
        strip would overlap content."""
        st = self._tip_state
        offs = st.lazy.section_offsets() if (st is not None and self.show_dict_tabs) else []
        if len(offs) >= 2:
            self._tab_names = [name for name, _ in offs]
            self._tab_offsets = [y for _, y in offs]
        else:
            self._tab_names, self._tab_offsets, self._tab_rects = [], [], []
            self._tab_bgra, self._tab_h = None, 0
        self._tab_active = -1  # force a strip + screen-rect rebuild on the next render

    def _active_section(self) -> int:
        """Index of the section the viewport currently shows (for the highlighted tab)."""
        active = 0
        for i, off in enumerate(self._tab_offsets):
            if off <= self._tip_scroll + self._tab_h + 1:
                active = i
        return active

    def _scroll_to_section(self, offset: int) -> None:
        if self._tip_bgra is None:
            return
        maxs = max(0, self._tip_bgra.shape[0] - self._tip_view_h)
        # the FIRST section's target is the panel top (headword visible); later sections tuck
        # their def-head just under the sticky tab row
        target = (
            0 if (self._tab_offsets and offset <= self._tab_offsets[0]) else offset - self._tab_h
        )
        self._tip_scroll = min(maxs, max(0, target))
        self._hide_at = 0.0  # navigating counts as interacting
        self._scan_target = None
        self._render_tip_view()

    def _tab_step(self, delta: int) -> None:
        if not self._tab_offsets:
            return
        idx = max(0, min(len(self._tab_offsets) - 1, self._active_section() + delta))
        self._scroll_to_section(self._tab_offsets[idx])

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
        if self._tip_bgra is None:
            return
        header = None
        if self._tab_names:
            active = self._active_section()
            if active != self._tab_active or self._tab_bgra is None:
                self._tab_active = active
                img, rects = render_tab_row(self._tab_names, active, int(self._tip_bgra.shape[1]))
                self._tab_bgra = to_bgra_array(img)
                self._tab_h = img.height
                tx, ty = self._tip_xy
                self._tab_rects = [(tx + x, ty + y, w, h) for x, y, w, h in rects]
            header = self._tab_bgra
        self._tip_rect = self._blit_panel(
            self._tip_bgra,
            self._tip_scroll,
            self._tip_view_h,
            self._tip_xy,
            OverlayId.TIP,
            header=header,
        )

    def _render_nested_view(self) -> None:
        nested_popup.render_nested_view(self)

    def _refresh_nested_full(self) -> None:
        nested_popup.refresh_nested_full(self)

    def _scroll_tip(self, delta: int) -> None:
        # route the wheel to whichever popup the cursor is over (nested sits on top)
        if self._nest.rect is not None and self._in_rect(self._nest.rect, *self._last_mouse):
            self._scroll_nested(delta)
            return
        if self._tip_bgra is None:
            return
        maxs = max(0, self._tip_bgra.shape[0] - self._tip_view_h)
        ns = min(maxs, max(0, self._tip_scroll + delta))
        if ns != self._tip_scroll:
            self._tip_scroll = ns
            self._hide_at = 0.0  # scrolling counts as interacting → keep it up
            self._scan_target = (
                None  # content moved under the cursor → restart the scan dwell (no clutter)
            )
            self._render_tip_view()

    def _scroll_nested(self, delta: int) -> None:
        nested_popup.scroll_nested(self, delta)

    # --- nested scanning: hover a word INSIDE the tooltip → its own popup -----------------------
    def _scan_hit(self, mx: float, my: float):
        """Which per-character scan cell of the base tooltip is under (mx, my)? Maps screen → panel
        coords (accounting for scroll) and returns the :class:`ScanBox`, or None."""
        st = self._tip_state
        if st is None or self._tip_rect is None:
            return None
        sx, sy = self._tip_xy
        px = mx - sx
        py = (my - sy) + self._tip_scroll
        for sb in st.lazy.scan_boxes:
            if sb.x <= px < sb.x + sb.w and sb.y <= py < sb.y + sb.h:
                return sb
        return None

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
        tracks = [t for t in (self._get("track-list") or []) if t.get("type") == "sub"]
        primary = self._get("sid")
        # prefer an English-tagged track; else any other sub track (generated demo subs carry no lang)
        pick = next((t for t in tracks if (t.get("lang") or "").lower() in EN_LANGS), None)
        if pick is None:
            pick = next((t for t in tracks if t.get("id") != primary), None)
        if pick is None:
            return None
        self.ipc.command("set_property", "secondary-sid", pick["id"])
        self.ipc.command("set_property", "secondary-sub-visibility", False)
        return pick["id"]

    def _translation_visible(self) -> bool:
        """Should the EN translation be shown now? Manual toggle (`t`), OR auto-reveal while a tooltip
        is up (auto-translate opt-in)."""
        return self._translate_on or (self.auto_translate and self.hover >= 0)

    def _sync_auto_translation(self) -> None:
        """Reconcile the auto-translation overlay with the hover state (only when opted in)."""
        if not self.auto_translate:
            return
        if self._translation_visible():
            self._draw_translation()
        elif not self._translate_on:
            self.ov.hide(OverlayId.TRANS)
            self._trans_text = None

    def toggle_translation(self) -> None:
        self._translate_on = not self._translate_on
        if self._translation_visible():
            self._draw_translation()
        else:
            self.ov.hide(OverlayId.TRANS)
            self._trans_text = None

    def _secondary_text(self) -> str:
        return (
            (self._prop("secondary-sub-text") or "").replace("\\N", " ").replace("\n", " ").strip()
        )

    def _draw_translation(self) -> None:
        text = self._secondary_text()
        self._trans_text = text
        if not text:
            self.ov.hide(OverlayId.TRANS)
            return
        size = max(20, round(self.osd[1] * 0.032))
        style = Style(size=size, color=(220, 224, 235, 255))
        pad = 14
        # trim the box to the text (wrap only if it exceeds 80% of the width), then centre it
        box_w = min(round(inline_width([Span(text, style)])) + 2 * pad, int(self.osd[0] * 0.8))
        flow = render_flow(
            [Span(text, style)], Block(width=box_w, padding=pad, background=(0, 0, 0, 170))
        )
        x = (self.osd[0] - flow.width) // 2
        # top of the screen (SubMiner-style) — separate from the JP subs at the bottom, and clear of
        # the tooltip that anchors above the hovered word.
        y = max(8, round(self.osd[1] * 0.035))
        self.ov.show(flow, x, y, oid=OverlayId.TRANS)

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
