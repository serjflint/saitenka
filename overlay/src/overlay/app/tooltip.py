"""The base tooltip: hover a subtitle word → look up its dictionary entry → show a scrollable,
per-dictionary-tabbed panel anchored to the word, with a header ⊕ (mine) / 🔊 (speak). Also owns the
hover-hysteresis state machine (word switches need a brief dwell; leaving the tooltip/nested-popup
area lingers before hiding) and the panel cache (LRU, keyed by :class:`PanelKey`).

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader. This
is the largest and most tightly-coupled extraction of the controller.py split (touches prefetch,
mining-mined-state, and the nested popup) — done last, after those neighbors had already shrunk and
clarified their own seams.
"""

from __future__ import annotations

import dataclasses as _dc
import time
from typing import TYPE_CHECKING, NamedTuple

from overlay import otel_metrics
from overlay.app.lookup import card_for, entry_for
from overlay.app.media import copy_clipboard, speak
from overlay.app.nested_popup import TIP_GAP
from overlay.app.overlay_ids import OverlayId
from overlay.app.perf import timed
from overlay.app.popups import TipPanel
from overlay.app.prefetch import FinishItem
from overlay.app.tokenize import AUX_POS
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

if TYPE_CHECKING:
    from overlay.app.controller import Reader

_HIT_TEST_SAMPLE_EVERY = 8  # OTel hit-test histogram samples 1-in-N poll ticks (unlike perf.timed,
# which is an unconditional deque append and stays on every tick)
FLASH_BGRA = (90, 214, 255, 255)  # premultiplied BGRA of the warm highlight (RGB 255,214,90)
JLPT_DARKEN = (
    0.62  # darken the pastel underline hue for the pill name-segment so white text is legible
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


# --- hover ---------------------------------------------------------------------------------------


def update_hover(reader: Reader) -> None:
    """Hover with hysteresis across the popup stack: keep each level alive while the cursor is on
    its trigger OR on the popup itself, lingering ``hide_delay`` after leaving both. Hovering a
    word *inside* the tooltip opens a nested scan popup."""

    with timed("hover_hit_test"):
        # Sampled, not every tick: this runs at poll cadence (~40Hz), and an OTel histogram
        # .record() call costs real cycles unlike perf.timed's plain deque append above.
        reader._hit_test_tick = (reader._hit_test_tick + 1) % _HIT_TEST_SAMPLE_EVERY
        if otel_metrics.hit_test_duration_ms is not None and reader._hit_test_tick == 0:
            # instrumented() (span + histogram) only on the sampled tick — a span every tick
            # would flood the trace at poll cadence for no visualization benefit.
            with otel_metrics.instrumented(otel_metrics.hit_test_duration_ms, "hit_test"):
                update_hover_impl(reader)
        else:
            update_hover_impl(reader)


def _hover_targets(reader: Reader, mx: float, my: float, inside: bool):
    """Which of (subtitle word, base tooltip, nested popup) the cursor is currently over."""
    over_word = reader._hit(mx, my) if (inside and reader.tokens) else -1
    over_tip = inside and reader._tip_rect is not None and reader._in_rect(reader._tip_rect, mx, my)
    over_nest = (
        inside and reader._nest.rect is not None and reader._in_rect(reader._nest.rect, mx, my)
    )
    return over_word, over_tip, over_nest


def _open_scan_popup(reader: Reader, scan) -> None:
    """A scan cell is under the cursor: open its nested popup once the dwell elapses."""
    now = time.monotonic()
    if scan.text != reader._scan_target:
        reader._scan_target, reader._scan_since = scan.text, now  # moved → restart the dwell
    # open only once the cursor has rested on this cell (scan delay), and it isn't already shown
    if now - reader._scan_since >= reader.scan_delay and reader._nest.tail != scan.text:
        reader._show_nested(scan)
    reader._nest.hide_at = 0.0


def _linger_nested(reader: Reader) -> None:
    """No scan cell under the cursor: let an already-open nested popup linger, then hide it."""
    reader._scan_target = None
    if reader._nest.state is None:
        return
    now = time.monotonic()
    if reader._nest.hide_at == 0.0:
        reader._nest.hide_at = now + reader.hide_delay
    elif now >= reader._nest.hide_at:
        reader._hide_nested()


def _update_nested_hover(
    reader: Reader, mx: float, my: float, over_tip: bool, over_nest: bool
) -> None:
    """Scan a word inside the tooltip; keep its popup alive while engaged. A cross-reference LINK is
    click-to-open, NOT hover-scan — so scrolling past / reading a link doesn't spawn scan popups
    that clutter the panel."""
    scan = scan_hit(reader, mx, my) if (over_tip and not over_nest) else None
    if scan is not None and reader._link_hit(
        mx, my, reader._tip_state, reader._tip_xy, reader._tip_scroll
    ):
        scan = None
    if scan is not None:
        _open_scan_popup(reader, scan)
    elif over_nest:
        reader._scan_target = None
        reader._nest.hide_at = 0.0
    else:
        _linger_nested(reader)


def _switch_word_hover(reader: Reader, over_word: int) -> None:
    """First open is instant, but SWITCHING to a different word needs a brief dwell — so dragging the
    cursor up to the tooltip across the OTHER line of a two-line sub doesn't hijack it onto every
    word it passes over. Only resting on a new word switches."""
    if over_word == reader.hover:
        reader._word_target = None
        return
    now = time.monotonic()
    if over_word != reader._word_target:
        reader._word_target, reader._word_since = over_word, now
    if reader.hover < 0 or now - reader._word_since >= reader.hover_switch_delay:
        reader.set_hover(over_word)
        reader._word_target = None


def _linger_word_hover(reader: Reader) -> None:
    """No word under the cursor: let the base tooltip linger, then hide it."""
    reader._word_target = None
    now = time.monotonic()
    if reader._hide_at == 0.0:
        reader._hide_at = now + reader.hide_delay
    elif now >= reader._hide_at:
        reader.set_hover(-1)
        reader._hide_at = 0.0


def _update_word_hover(reader: Reader, over_word: int, over_tip: bool, over_nest: bool) -> None:
    """Base tooltip: also kept alive while the cursor is on the nested popup."""
    if over_word >= 0:
        _switch_word_hover(reader, over_word)
        reader._hide_at = 0.0
    elif over_tip or over_nest:
        reader._hide_at = 0.0  # resting on the tooltip or its scan popup → keep it alive
        reader._word_target = None
    elif reader.hover != -1:
        _linger_word_hover(reader)


def update_hover_impl(reader: Reader) -> None:
    mp = reader._prop("mouse-pos") or {}
    inside = bool(mp.get("hover"))
    reader._mouse_in = inside  # engagement signal for prefetch
    mx, my = mp.get("x", -1), mp.get("y", -1)
    reader._last_mouse = (mx, my)
    over_word, over_tip, over_nest = _hover_targets(reader, mx, my, inside)
    _update_nested_hover(reader, mx, my, over_tip, over_nest)
    _update_word_hover(reader, over_word, over_tip, over_nest)


def set_hover(reader: Reader, index: int) -> None:
    if index == reader.hover:
        return
    reader.hover = index
    reader._draw_subtitle()
    if index < 0:
        reader._teardown_tip()  # hide OverlayId.TIP/OverlayId.NESTED, reset all state, release pause
        return
    show_tooltip(reader, index)
    reader._sync_auto_translation()  # hovering a word → auto-reveal the translation


def speak_hovered(reader: Reader) -> None:
    # speak the DICTIONARY-form reading (習う → ならう), not the kanji surface (say reads 習 as
    # しゅう → "shuuwa") nor the bare stem reading ならわ. Falls back to the token reading/surface.
    if 0 <= reader.hover < len(reader.tokens):
        t = reader.tokens[reader.hover]
        speak(reader._hover_reading or t.reading or t.surface)


def copy_hovered(reader: Reader) -> None:
    if 0 <= reader.hover < len(reader.tokens):
        copy_token(reader, reader.tokens[reader.hover])


def token_clip(t) -> str:
    return f"{t.surface}【{t.reading}】" if t.reading else t.surface


def copy_token(reader: Reader, t) -> None:
    copy_clipboard(token_clip(t))
    reader._toast(f"copied {t.surface}", "ok", 1.2)


def flash(reader: Reader, oid: int) -> None:
    """Pulse a "copied" highlight border on a popup as copy feedback, then let the poll loop
    restore it after ``flash_secs``."""
    reader._flash_oid = oid
    reader._flash_until = time.monotonic() + reader.flash_secs
    reader._render_nested_view() if oid == OverlayId.NESTED else render_tip_view(reader)


def copy_click(reader: Reader) -> None:
    """Right-click — copy the word under the cursor (the inner scanned word if over the nested
    popup, else the hovered/pointed subtitle word), with a brief highlight flash."""
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    if reader._nest.rect is not None and reader._in_rect(reader._nest.rect, x, y):
        if reader._nest.token is not None:
            copy_token(reader, reader._nest.token)
            flash(reader, OverlayId.NESTED)
        return
    if reader._tip_rect is not None and reader._in_rect(reader._tip_rect, x, y):
        copy_hovered(reader)
        flash(reader, OverlayId.TIP)
        return
    idx = reader._hit(x, y) if reader.tokens else -1  # not over a popup → the subtitle word, if any
    if idx >= 0:
        copy_token(reader, reader.tokens[idx])


# --- header hit-testing (⊕ / 🔊, shared by base tooltip and nested popup) -------------------------


def hit_header_region(
    reader: Reader, x: float, y: float, prect, xy, scroll: int, view_h: int
) -> bool:
    """Is (x, y) on a header button (panel-space ``prect``)? Only while it's inside the scrolled
    viewport (the header scrolls off). Shared by the base tooltip and the nested popup."""
    px, py, pw, ph = prect
    top = py - scroll
    if top < 0 or top + ph > view_h:  # header scrolled out of the viewport
        return False
    sx, sy = xy
    return reader._in_rect((sx + px, sy + top, pw, ph), x, y)


def tip_reserve(reader: Reader) -> int:
    """The base tooltip's tab-strip top-reserve (0 when no tabs) — header hit-boxes must match it."""
    return reader._tip_state.lazy.top_reserve if reader._tip_state is not None else 0


def hit_header_add(reader: Reader, x: float, y: float) -> bool:
    if reader._tip_state is None or not anki_ok(reader):  # ⊕ only when Anki is reachable now
        return False
    return hit_header_region(
        reader,
        x,
        y,
        header_add_rect(
            reader.tip_width, top_reserve=tip_reserve(reader), speak_button=reader._tts_ok
        ),
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_view_h,
    )


def hit_header_speaker(reader: Reader, x: float, y: float) -> bool:
    if reader._tip_state is None or not reader._tts_ok:  # 🔊 hidden when no JA TTS voice
        return False
    return hit_header_region(
        reader,
        x,
        y,
        header_speaker_rect(reader.tip_width, top_reserve=tip_reserve(reader)),
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_view_h,
    )


def hit_nested_add(reader: Reader, x: float, y: float) -> bool:
    if reader._nest.state is None or not anki_ok(reader):
        return False
    return hit_header_region(
        reader,
        x,
        y,
        header_add_rect(reader.tip_width, speak_button=reader._tts_ok),
        reader._nest.xy,
        reader._nest.scroll,
        reader._nest.view_h,
    )


def hit_nested_speaker(reader: Reader, x: float, y: float) -> bool:
    if reader._nest.state is None or not reader._tts_ok:  # 🔊 hidden when no JA TTS voice
        return False
    return hit_header_region(
        reader,
        x,
        y,
        header_speaker_rect(reader.tip_width),
        reader._nest.xy,
        reader._nest.scroll,
        reader._nest.view_h,
    )


# --- click routing ---------------------------------------------------------------------------------


def _click_nested(reader: Reader, x: float, y: float) -> bool:
    """Handle a click landing on the nested popup. Returns True if it did (regardless of what, if
    anything, it hit) so the caller doesn't fall through to the base tooltip underneath."""
    if reader._nest.rect is None or not reader._in_rect(reader._nest.rect, x, y):
        return False
    if hit_nested_add(reader, x, y) and reader._nest.token is not None:
        reader._mine_token(reader._nest.token)  # ⊕ → mine the *inner* (scanned) word
    elif hit_nested_speaker(reader, x, y) and reader._nest.state:
        speak(reader._nest.state.reading)  # 🔊 → read the inner word aloud
    else:
        lb = reader._link_hit(x, y, reader._nest.state, reader._nest.xy, reader._nest.scroll)
        if lb is not None:
            reader._open_link(lb, reader._nest.xy, reader._nest.scroll)  # cross-ref → navigate
    return True


def _click_tip(reader: Reader, x: float, y: float) -> bool:
    """Handle a click landing on the base tooltip. Returns True if it did."""
    if reader._tip_rect is None or not reader._in_rect(reader._tip_rect, x, y):
        return False
    # Header ⊕/🔊 are specific top-right affordances — they win over the general dict-tab band,
    # which spans the full width and would otherwise steal a click that overlaps them.
    if hit_header_add(reader, x, y):
        reader.mine_current()  # ⊕ → mine the hovered word into Anki
        return True
    if hit_header_speaker(reader, x, y):
        reader.speak_hovered()  # 🔊 → hear the word (TTS)
        return True
    for rect, off in zip(reader._tab_rects, reader._tab_offsets, strict=False):
        if reader._in_rect(rect, x, y):  # dict tab → jump the viewport to that section
            scroll_to_section(reader, off)
            return True
    lb = reader._link_hit(x, y, reader._tip_state, reader._tip_xy, reader._tip_scroll)
    if lb is not None:
        reader._open_link(lb, reader._tip_xy, reader._tip_scroll)  # cross-ref → nested popup
    else:
        reader._click_kanji_fallback(x, y)  # single-ideograph cell → kanji entry
    return True


def on_click(reader: Reader) -> None:
    # Left-click drives buttons only — the card preview's ✕/screenshot/▶, and each popup's ⊕/🔊.
    # Clicking an empty area does NOTHING: audio must not fire on a stray body click.
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    if reader._click_preview(x, y):
        return
    if _click_nested(reader, x, y):  # the nested popup sits on top → test it first
        return
    _click_tip(reader, x, y)


# --- panel building ----------------------------------------------------------------------------


def inflected_surface(reader: Reader, index: int) -> str:
    """Token surface + trailing auxiliary tokens (助動詞), so the chain deinflects the full word
    (習わ + ぬ → 習わぬ); the tokenizer splits inflected verbs from their auxiliaries."""
    s = reader.tokens[index].surface
    j = index + 1
    while j < len(reader.tokens) and reader.tokens[j].pos in AUX_POS:
        s += reader.tokens[j].surface
        j += 1
    return s


def panel_key(reader: Reader, tok, inflected, mined: bool = False, tabs: bool = True) -> PanelKey:
    # anki_ok is live (rebuilds the cached panel when Anki opens/closes; stable within its ~3s TTL).
    # ``tabs`` distinguishes the base build (with the dict-tab reserve) from a nested build (none),
    # so the same word shown in both places doesn't share the wrong reserve.
    return PanelKey(
        tok.lemma,
        tok.surface,
        tok.reading,
        inflected,
        reader.tip_width,
        anki_ok(reader),
        mined,
        tabs,
    )


def is_mined(reader: Reader, tok) -> bool:
    """Is this token's word already in the deck? (its ⊕ shows ✓ instead). Cheap short-circuit
    while nothing has been mined; else a card_for lookup (lru-cached)."""
    if not reader._mined:
        return False
    try:
        return card_for(tok).expression in reader._mined
    except Exception:
        return False


def anki_ok(reader: Reader) -> bool:
    """Is AnkiConnect reachable RIGHT NOW? Gates the ⊕ button per card show, so it appears/hides as
    the user opens/closes Anki mid-session (not frozen at startup). Kept fast: a short timeout with
    0 retries fails immediately when Anki is closed, and the result is cached ``anki_ok_ttl``
    seconds so rapid hovers don't ping repeatedly. False when mining isn't configured at all."""
    if reader.anki is None:
        return False
    now = time.monotonic()
    ts, ok = reader._anki_cache
    if now - ts < reader.anki_ok_ttl:
        return ok
    from overlay.app.anki import anki_reachable

    ok = anki_reachable(
        timeout=reader.anki_ping_timeout
    )  # resolves host/key from config; 0 retries
    reader._anki_cache = (now, ok)
    return ok


def _darken(rgba, f: float = JLPT_DARKEN):
    r, g, b, a = rgba
    return (round(r * f), round(g * f), round(b * f), a)


def jlpt_pill(reader: Reader, tok) -> Freq | None:
    """A ``JLPT | Nx`` pill for the tooltip's frequency row, shown only when the word has a JLPT
    level — the same signal the subtitle draws as an underline (``Scorer._style``). The pill's hue
    is the level's underline color (darkened for legible white text), so the tooltip and the
    underline read as the same thing."""
    from overlay.app.scoring import _is_content

    sc = reader.scorer
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
    return Freq("JLPT", level, _darken(base))


def entry_for_tok(reader: Reader, tok, inflected):
    """Look up the panel entry and fold in the JLPT pill (near the frequency pills) when the word
    carries a JLPT level, so it mirrors the subtitle underline.

    Never mutates the lru_cached Entry from lookup.lookup_entry / dict_set.entry_for — returns
    a shallow copy with a new freqs list so repeated calls do not accumulate pills."""
    entry = reader.dict_set.entry_for(tok, inflected) if reader.dict_set else entry_for(tok)
    pill = jlpt_pill(reader, tok)
    if pill is not None and hasattr(entry, "freqs"):
        # Build the pill list into a shallow copy — never mutate the cached original.
        entry = _dc.replace(entry, freqs=[pill, *entry.freqs])
    return entry


def finish_available(reader: Reader) -> bool:
    """A running prefetch worker can render a tooltip's deferred tail. Without one (prefetch off,
    or before the workers start) we finish synchronously so a partial panel never gets stuck."""
    return bool(reader.prefetch and reader._prefetch_threads)


def _build_panel(
    reader: Reader, key: PanelKey, tok, inflected, mined: bool, tabs: bool
) -> TipPanel:
    if otel_metrics.panel_cache_misses is not None:
        otel_metrics.panel_cache_misses.add(1)
    with otel_metrics.instrumented(otel_metrics.render_duration_ms, "render"):
        entry = entry_for_tok(reader, tok, inflected)
        # Reserve space for the sticky dict-tab strip (base tooltip, ≥2 dicts, tabs on) so it clears
        # the header (reading + ⊕/🔊) instead of overlapping it. Use the WRAPPED height for this
        # word's dict names at this width, so a many-dict strip that wraps onto several rows
        # reserves enough. Nested popups (tabs=False) reserve nothing.
        reserve = (
            tab_strip_height([d.dict_name for d in entry.defs], reader.tip_width)
            if (tabs and len(entry.defs) >= 2)
            else 0
        )
        lazy = LazyPanel(
            panel_rows(
                entry,
                reader.tip_width,
                add_button=anki_ok(reader),
                mined=mined,
                speak_button=reader._tts_ok,
            ),
            reader.tip_width,
            top_reserve=reserve,
        )
        return TipPanel(lazy, getattr(entry, "reading", "") or tok.reading)


def _panel_cache_get(
    reader: Reader, key: PanelKey, tok, inflected, mined: bool, tabs: bool
) -> TipPanel:
    st = reader._panel_cache.get(key)
    if st is None:
        st = _build_panel(reader, key, tok, inflected, mined, tabs)
        with reader._cache_lock:
            st = panel_cache_setdefault(reader, key, st)
    else:
        if otel_metrics.panel_cache_hits is not None:
            otel_metrics.panel_cache_hits.add(1)
        # Cache hit: move to end (most-recently-used) under the lock so the LRU order stays accurate.
        with reader._cache_lock:
            try:
                reader._panel_cache.move_to_end(key)
            except KeyError:
                pass  # evicted between get() and move_to_end() — harmless
    return st


def panel_for(
    reader: Reader,
    tok,
    inflected=None,
    min_h: int | None = None,
    finish: bool = False,
    mined: bool | None = None,
    tabs: bool | None = None,
):
    """The memoised :class:`TipPanel` for a token. ``finish`` renders the whole entry (prefetch /
    no-worker path); otherwise only the head that fills ``min_h`` px is rendered now (viewport-first)
    and the tail is deferred. Re-hovering is instant and scrolling is cheap. ``mined`` (default: look
    it up) selects the ⊕ vs ✓ header variant and is part of the cache key. ``tabs`` (default: the
    ``show_dict_tabs`` config) reserves + will draw the sticky dict-tab strip; a nested popup passes
    ``tabs=False`` so it carries no strip and no reserved band.

    Thread-safe: the panel is *built* lock-free (thread-local DB conns + fonts, each render owns its
    images), and only the tiny cache write/LRU update is locked. On a free-threaded (no-GIL) build,
    OrderedDict.get() is NOT atomic, so cache hits also acquire the lock briefly to move_to_end.
    Hovers remain snappy because the lock is held for only a few microseconds (no rendering inside)."""
    if mined is None:
        mined = is_mined(reader, tok)
    if tabs is None:
        tabs = reader.show_dict_tabs
    key = panel_key(reader, tok, inflected, mined, tabs)
    st = _panel_cache_get(reader, key, tok, inflected, mined, tabs)
    if finish:
        st.finish()
    else:
        st.render_head(min_h if min_h is not None else reader._tip_cap())
    return st


def panel_cache_setdefault(reader: Reader, key: PanelKey, st: TipPanel) -> TipPanel:
    """Insert ``st`` for ``key`` if not already present; evict the LRU entry when over the cap.
    Must be called under ``reader._cache_lock``. First-writer-wins: if two workers race to build
    the same panel, the winner's result is kept and the loser is discarded (both are equivalent)."""
    if key in reader._panel_cache:
        reader._panel_cache.move_to_end(key)
        return reader._panel_cache[key]
    # Evict least-recently-used entries until we are at the limit.
    while len(reader._panel_cache) >= reader.panel_cache_max:
        reader._panel_cache.popitem(last=False)  # FIFO/LRU: oldest (first) entry out
    reader._panel_cache[key] = st
    return st


# --- showing / placing / rendering the base tooltip ---------------------------------------------


def show_tooltip(reader: Reader, index: int) -> None:

    with timed("show_tooltip"):
        show_tooltip_impl(reader, index)


def show_tooltip_impl(reader: Reader, index: int) -> None:
    reader._hide_nested()  # switching the base word drops any stale scan popup
    reader._kanji_index = 0  # a new word restarts the `k` kanji cycle
    tok = reader.tokens[index]
    inflected = inflected_surface(reader, index)
    cap = reader._tip_cap()
    # Viewport-first: paint only the head that fills the viewport now; a worker renders the tail.
    # Without a worker (prefetch off) finish synchronously so the panel is never left partial.
    mined = is_mined(reader, tok)
    key = panel_key(reader, tok, inflected, mined)
    st = panel_for(
        reader, tok, inflected, min_h=cap, finish=not reader._finish_available(), mined=mined
    )
    reader._tip_state, reader._tip_key, reader._tip_dirty = st, key, False
    reader._tip_bgra = st.bgra()  # decompress the cached panel into the active scroll buffer
    reader._hover_reading = st.reading
    reader._tip_scroll = 0

    ox, oy = reader.sub_origin
    b = reader.boxes[index]
    wx, wy = ox + b.x, oy + b.y
    # Safe area: keep clear of the OSC/window header at the top and the controls/edge at the bottom,
    # so the tooltip never spills under the window chrome. It scrolls, so we cap the height rather
    # than trying to fit the whole (very tall) entry.
    assert st.ready  # head render above guarantees the panel is stored
    ph, pw = st.shape[0], st.shape[1]
    reader._tip_view_h = min(ph, cap)
    reader._tip_xy = place_panel(reader, pw, wx, wy, b.h, reader._tip_view_h)
    update_tabs(reader)
    render_tip_view(reader)
    reader._bind_tip_keys()  # LEFT/RIGHT/UP/DOWN/ESC live only while the tip shows
    if not st.complete:
        reader._finish_q.put(FinishItem(st, key))  # worker fills the tail → _tip_dirty → refresh
    if reader.pause_on_tooltip and not reader._paused_by_tip and not reader._prop("pause"):
        reader.ipc.command("set_property", "pause", True)  # freeze the frame while you read
        reader._paused_by_tip = True


def place_panel(
    reader: Reader, full_w: int, wx: float, wy: float, wh: float, view_h: int
) -> tuple[int, int]:
    """Choose a top-left (tx, ty) for a panel of width ``full_w`` and height ``view_h`` anchored to
    an on-screen word box (wx, wy, wh): above it if there's room, else below, clamped to the safe
    area. Shared by the base tooltip and nested popups."""

    margin = max(16, round(reader.osd[1] * 0.05))
    above_room = wy - TIP_GAP - margin
    below_room = (reader.osd[1] - margin) - (wy + wh + TIP_GAP)
    if above_room >= view_h or above_room >= below_room:
        ty = wy - TIP_GAP - view_h  # above the word
    else:
        ty = wy + wh + TIP_GAP  # below the word
    tx = max(margin, min(wx, reader.osd[0] - full_w - margin))
    ty = max(margin, min(ty, reader.osd[1] - margin - view_h))
    return int(tx), int(ty)


def refresh_tip_full(reader: Reader) -> None:
    """A background finish grew the shown panel (deferred bodies rendered) → re-upload the view so
    the scrollbar reflects the true height and the below-the-fold content is scrollable."""
    st = reader._tip_state
    if st is None or not st.ready:
        return
    reader._tip_bgra = st.bgra()  # re-decompress the grown panel into the active scroll buffer
    update_tabs(reader)  # the streamed tail may add sections / move offsets
    render_tip_view(reader)


def blit_panel(reader: Reader, bgra, scroll: int, view_h: int, xy, oid: int, header=None):
    """Upload a scrolled viewport slice of a premultiplied BGRA panel to an OSD overlay, drawing a
    scrollbar thumb when the panel is taller than the viewport. ``header`` is an opaque BGRA strip
    composited over the TOP of the viewport — the sticky dict-tab row. Returns the shown screen rect."""
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
    if reader._flash_oid == oid and time.monotonic() < reader._flash_until:
        b = 4  # "copied" highlight border (a brief visual pulse)
        view[:b, :] = view[-b:, :] = FLASH_BGRA
        view[:, :b] = view[:, -b:] = FLASH_BGRA
    tx, ty = xy
    reader.ov.show_bgra(view, tx, ty, oid=oid)
    return (tx, ty, full_w, view.shape[0])


# --- per-dictionary tabs -------------------------------------------------------------------------


def update_tabs(reader: Reader) -> None:
    """Recompute the dict-tab sections from the shown panel (≥2 sections → tabs). Off entirely when
    ``show_dict_tabs`` is disabled — the panel is then built without the reserve too, so drawing a
    strip would overlap content."""
    st = reader._tip_state
    offs = st.lazy.section_offsets() if (st is not None and reader.show_dict_tabs) else []
    if len(offs) >= 2:
        reader._tab_names = [name for name, _ in offs]
        reader._tab_offsets = [y for _, y in offs]
    else:
        reader._tab_names, reader._tab_offsets, reader._tab_rects = [], [], []
        reader._tab_bgra, reader._tab_h = None, 0
    reader._tab_active = -1  # force a strip + screen-rect rebuild on the next render


def active_section(reader: Reader) -> int:
    """Index of the section the viewport currently shows (for the highlighted tab)."""
    active = 0
    for i, off in enumerate(reader._tab_offsets):
        if off <= reader._tip_scroll + reader._tab_h + 1:
            active = i
    return active


def scroll_to_section(reader: Reader, offset: int) -> None:
    if reader._tip_bgra is None:
        return
    maxs = max(0, reader._tip_bgra.shape[0] - reader._tip_view_h)
    # the FIRST section's target is the panel top (headword visible); later sections tuck their
    # def-head just under the sticky tab row
    target = (
        0 if (reader._tab_offsets and offset <= reader._tab_offsets[0]) else offset - reader._tab_h
    )
    reader._tip_scroll = min(maxs, max(0, target))
    reader._hide_at = 0.0  # navigating counts as interacting
    reader._scan_target = None
    render_tip_view(reader)


def tab_step(reader: Reader, delta: int) -> None:
    if not reader._tab_offsets:
        return
    idx = max(0, min(len(reader._tab_offsets) - 1, active_section(reader) + delta))
    scroll_to_section(reader, reader._tab_offsets[idx])


def render_tip_view(reader: Reader) -> None:
    if reader._tip_bgra is None:
        return
    header = None
    if reader._tab_names:
        active = active_section(reader)
        if active != reader._tab_active or reader._tab_bgra is None:
            reader._tab_active = active
            img, rects = render_tab_row(reader._tab_names, active, int(reader._tip_bgra.shape[1]))
            reader._tab_bgra = to_bgra_array(img)
            reader._tab_h = img.height
            tx, ty = reader._tip_xy
            reader._tab_rects = [(tx + x, ty + y, w, h) for x, y, w, h in rects]
        header = reader._tab_bgra
    reader._tip_rect = blit_panel(
        reader,
        reader._tip_bgra,
        reader._tip_scroll,
        reader._tip_view_h,
        reader._tip_xy,
        OverlayId.TIP,
        header=header,
    )


def scroll_tip(reader: Reader, delta: int) -> None:
    # route the wheel to whichever popup the cursor is over (nested sits on top)
    if reader._nest.rect is not None and reader._in_rect(reader._nest.rect, *reader._last_mouse):
        reader._scroll_nested(delta)
        return
    if reader._tip_bgra is None:
        return
    maxs = max(0, reader._tip_bgra.shape[0] - reader._tip_view_h)
    ns = min(maxs, max(0, reader._tip_scroll + delta))
    if ns != reader._tip_scroll:
        reader._tip_scroll = ns
        reader._hide_at = 0.0  # scrolling counts as interacting → keep it up
        reader._scan_target = None  # content moved under the cursor → restart the scan dwell
        render_tip_view(reader)


# --- nested scanning: hover a word INSIDE the tooltip → its own popup -----------------------------


def scan_hit(reader: Reader, mx: float, my: float):
    """Which per-character scan cell of the base tooltip is under (mx, my)? Maps screen → panel
    coords (accounting for scroll) and returns the :class:`~overlay.model.ScanBox`, or None."""
    st = reader._tip_state
    if st is None or reader._tip_rect is None:
        return None
    sx, sy = reader._tip_xy
    px = mx - sx
    py = (my - sy) + reader._tip_scroll
    for sb in st.lazy.scan_boxes:
        if sb.x <= px < sb.x + sb.w and sb.y <= py < sb.y + sb.h:
            return sb
    return None
