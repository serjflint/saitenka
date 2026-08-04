"""The base tooltip: hover a subtitle word → look up its dictionary entry → show a scrollable panel
anchored to the word, with a header ⊕ (mine) / 🔊 (speak). Also owns the hover-hysteresis state
machine (word switches need a brief dwell; leaving the tooltip/nested-popup area lingers before
hiding), the panel cache (LRU, keyed by :class:`PanelKey`), and the windowed (banded) render path —
the sole tooltip compositor, shared by the base tooltip and every nested/kanji/search popup.

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader. This
is the largest and most tightly-coupled extraction of the controller.py split (touches prefetch,
mining-mined-state, and the nested popup) — done last, after those neighbors had already shrunk and
clarified their own seams.
"""

from __future__ import annotations

import dataclasses as _dc
import logging
import time
from typing import TYPE_CHECKING, NamedTuple

from overlay import otel_metrics
from overlay.app import prefetch
from overlay.app.lookup import card_for, entry_for
from overlay.app.media import copy_clipboard, speak
from overlay.app.nested_popup import TIP_GAP
from overlay.app.overlay_ids import OverlayId
from overlay.app.perf import timed
from overlay.app.popups import Panel
from overlay.app.tokenize import phrase_terms, query_token
from overlay.panel import Freq, header_add_rect, header_speaker_rect, panel_rows

if TYPE_CHECKING:
    from overlay.app.controller import Reader

_HIT_TEST_SAMPLE_EVERY = 8  # OTel hit-test histogram samples 1-in-N poll ticks (unlike perf.timed,
# which is an unconditional deque append and stays on every tick)
FLASH_BGRA = (90, 214, 255, 255)  # premultiplied BGRA of the warm highlight (RGB 255,214,90)
JLPT_DARKEN = (
    0.62  # darken the pastel underline hue for the pill name-segment so white text is legible
)
log = logging.getLogger(__name__)  # DEBUG lands in overlay.log → bundled by `saitenka report`


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
    # Per-stacked-entry mined state (aligned to cards_for order): flips a group's ⊕→✓ and, as part of
    # the key, rebuilds the panel when one stacked entry gets mined. () for single-entry words.
    group_mined: tuple[bool, ...] = ()
    # Longer multi-token phrase terms stacked above the bare word (数ある over 数); part of the key so
    # the same word hovered mid-phrase vs standalone caches distinctly. () when no longer term.
    phrase_terms: tuple[str, ...] = ()


# --- hover -----------------------------------------------------------------------------------------


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


def _hover_targets(reader: Reader, mx: float, my: float, *, inside: bool):
    """Which of (subtitle word, base tooltip, nested popup) the cursor is currently over."""
    over_tip = inside and reader._tip_rect is not None and reader._in_rect(reader._tip_rect, mx, my)
    over_nest = (
        inside and reader._nest.rect is not None and reader._in_rect(reader._nest.rect, mx, my)
    )
    # The popups are drawn ON TOP of the subtitle, so a hit on a popup occludes the word beneath it:
    # keep the lease on the open tooltip instead of switching to the word it happens to cover (e.g. the
    # tooltip for the lower line, drawn up over the upper line of a two-line cue). Without this the base
    # hit-test still sees that covered word and `hover_switch_delay` only *delays* the hijack.
    over_word = (
        reader._hit(mx, my) if (inside and reader.tokens and not (over_tip or over_nest)) else -1
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
    reader: Reader, mx: float, my: float, *, over_tip: bool, over_nest: bool
) -> None:
    """Scan a word inside the tooltip; keep its popup alive while engaged. A cross-reference LINK is
    click-to-open, NOT hover-scan — so scrolling past / reading a link doesn't spawn scan popups
    that clutter the panel."""
    scan = scan_hit(reader, mx, my) if (over_tip and not over_nest) else None
    if scan is not None and reader._tip_link_hit(mx, my):
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


def _update_word_hover(reader: Reader, over_word: int, *, over_tip: bool, over_nest: bool) -> None:
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
    over_word, over_tip, over_nest = _hover_targets(reader, mx, my, inside=inside)
    reader.set_annotation_hover(revealed=over_word >= 0)
    _update_nested_hover(reader, mx, my, over_tip=over_tip, over_nest=over_nest)
    _update_word_hover(reader, over_word, over_tip=over_tip, over_nest=over_nest)


def resolve_hover(reader: Reader, index: int) -> None:
    """Set the hovered word's stacked phrase terms + highlight span. Multi-token dictionary terms
    starting at ``index`` (数ある over 数) are looked up as extra terms on the hovered word, so the
    tooltip stacks them above the bare word and the underline spans the longest. Runs before the
    subtitle redraw so the highlight covers the span on the first paint."""
    terms: tuple[str, ...] = ()
    span: tuple[int, int] | None = None
    has_term = getattr(
        reader.dict_set, "has_term", None
    )  # phrase merge is an optional dict capability
    if has_term is not None:
        got = phrase_terms(tokens=reader.tokens, index=index, has_term=has_term)
        if got is not None:
            term_list, start, end = got
            terms, span = tuple(term_list), (start, end)
    reader._hover_terms = terms
    reader._hover_span = span


def set_hover(reader: Reader, index: int) -> None:
    if index == reader.hover:
        return
    reader.hover = index
    if index < 0:
        reader._hover_terms = ()
        reader._hover_span = None
        reader._draw_subtitle()
        reader._teardown_tip()  # hide OverlayId.TIP/OverlayId.NESTED, reset all state, release pause
        return
    resolve_hover(reader, index)  # sets _hover_terms/_hover_span BEFORE the draw highlights it
    reader._draw_subtitle()
    show_tooltip(reader, index)
    if reader._session_recorder is not None:
        reader._session_recorder.record_lookup()
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
    if top < 0 or top + ph > view_h:  # header scrolled out of the viewport (all in reference px)
        return False
    sx, sy = xy
    s = reader._tip_display_scale  # panel-space rect → display px (origin is already display px)
    return reader._in_rect((sx + px * s, sy + top * s, pw * s, ph * s), x, y)


def hit_header_add(reader: Reader, x: float, y: float) -> bool:
    if reader._tip_state is None or not anki_ok(reader):  # ⊕ only when Anki is reachable now
        return False
    return hit_header_region(
        reader,
        x,
        y,
        header_add_rect(reader.tip_width, speak_button=reader._tts_ok),
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
        header_speaker_rect(reader.tip_width),
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


# --- click routing -----------------------------------------------------------------------------


def _mine_link(reader: Reader, lb, tok) -> bool:
    """A stacked entry's ⊕ arrives as a ``LinkBox('mine:<card_index>')`` (it rides the normal link
    hit-test). Mine that exact entry via ``cards_for(tok)[i]`` and report handled, so the caller does
    not treat it as a cross-reference navigation. Not a mine link → False."""
    if (
        tok is None
        or not isinstance(getattr(lb, "query", None), str)
        or not lb.query.startswith("mine:")
    ):
        return False
    # Same expanded card list the stacked panel was built from (phrase terms included), so the ⊕'s
    # card_index aligns with the group it sits on.
    cards = (
        reader.dict_set.cards_for(tok, extra_terms=reader._hover_terms) if reader.dict_set else []
    )
    idx = int(lb.query[len("mine:") :])
    if 0 <= idx < len(cards):
        reader._mine_token(tok, card=cards[idx])
    return True


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
        lb = reader._nest_link_hit(x, y)
        if lb is not None and not _mine_link(reader, lb, reader._nest.token):
            reader._open_link(lb, reader._nest.xy, reader._nest.scroll)  # cross-ref → navigate
    return True


def _click_tip(reader: Reader, x: float, y: float) -> bool:
    """Handle a click landing on the base tooltip. Returns True if it did."""
    if reader._tip_rect is None or not reader._in_rect(reader._tip_rect, x, y):
        return False
    if hit_header_add(reader, x, y):
        reader.mine_current()  # ⊕ → mine the hovered word into Anki
        return True
    if hit_header_speaker(reader, x, y):
        reader.speak_hovered()  # 🔊 → hear the word (TTS)
        return True
    lb = reader._tip_link_hit(x, y)
    if lb is not None:
        tok = reader.tokens[reader.hover] if 0 <= reader.hover < len(reader.tokens) else None
        if not _mine_link(reader, lb, tok):  # stacked entry ⊕ → mine that entry
            reader._navigate_tip(lb.query)  # cross-ref → replace base content in place (Yomitan)
    else:
        reader._click_kanji_fallback(x, y)  # single-ideograph cell → kanji entry
    return True


def on_click(reader: Reader) -> None:
    # Left-click drives buttons only — the card preview's ✕/screenshot/▶, and each popup's ⊕/🔊.
    # Clicking an empty area does NOTHING: audio must not fire on a stray body click.
    mp = reader._get("mouse-pos") or {}
    x, y = mp.get("x", -1), mp.get("y", -1)
    in_tip = reader._tip_rect is not None and reader._in_rect(reader._tip_rect, x, y)
    if reader._click_preview(x, y):
        captured = "preview"
    elif _click_nested(reader, x, y):  # the nested popup sits on top → test it first
        captured = "nested"
    elif _click_tip(reader, x, y):
        captured = "tip"
    else:
        captured = "none"  # fell through — nothing under the click
    # Diagnostic: correlate a click with whether it landed on the tip rect and the pause lease, so the
    # report shows if a click while paused misses _tip_rect (mouse-pos↔OSD mismatch) or tears the tip down.
    log.debug(
        "click at (%.0f,%.0f) hover=%s in_tip=%s captured=%s tip_rect=%s paused_by_tip=%s mpv_pause=%s",
        x,
        y,
        bool(mp.get("hover")),
        in_tip,
        captured,
        reader._tip_rect,
        reader._paused_by_tip,
        reader._prop("pause"),
    )


# --- panel building ----------------------------------------------------------------------------


def panel_key(
    reader: Reader,
    tok,
    inflected,
    *,
    mined: bool = False,
    phrase: tuple[str, ...] = (),
) -> PanelKey:
    # anki_ok is live (rebuilds the cached panel when Anki opens/closes; stable within its ~3s TTL).
    # ``phrase`` is the word's stacked multi-token terms — the base word's, or a nested scan's
    # longest-match under the cursor (コンサート over コン); empty for prefetch and clicked links.
    return PanelKey(
        tok.lemma,
        tok.surface,
        tok.reading,
        inflected,
        reader.tip_width,
        anki_ok(reader),
        mined,
        group_mined_of(reader=reader, tok=tok, extra_terms=phrase),
        # the stacked phrase terms are part of the base panel's identity (数 alone vs 数 under 数ある)
        phrase,
    )


def is_mined(reader: Reader, tok) -> bool:
    """Is this token's word already in the deck? (its ⊕ shows ✓ instead). Cheap short-circuit
    while nothing has been mined; else a card_for lookup (lru-cached)."""
    if not reader._mined:
        return False
    try:
        return card_for(tok).expression in reader._mined
    except Exception:  # noqa: BLE001  # render hot path - any lookup hiccup just hides the mined mark
        return False


def group_mined_of(reader: Reader, tok, *, extra_terms: tuple[str, ...] = ()) -> tuple[bool, ...]:
    """Per-stacked-entry mined flags (aligned to ``cards_for`` order) for a multi-reading word — each
    entry's ⊕ shows ✓ when that exact (expression, reading) is already in the deck. () when nothing is
    mined yet (cheap short-circuit) or the word has fewer than two entries (no stacking).
    ``extra_terms`` must match the panel's phrase stacking so the flags align with the shown groups."""
    if not reader._mined or reader.dict_set is None:
        return ()
    try:
        cards = reader.dict_set.cards_for(tok, extra_terms=extra_terms)
    except Exception:  # noqa: BLE001  # render hot path - a lookup hiccup just hides the mined marks
        return ()
    if len(cards) < 2:
        return ()
    return tuple(c.expression in reader._mined for c in cards)


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


def rareness_pill(reader: Reader, tok) -> Freq | None:
    """The blended-rareness "diff" pill: harmonic mean of the word's rank across every loaded freq
    dict, colored by band (:func:`fsrs.rareness_color`). Summarizes the row of 7+ per-dict pills into
    one rareness read. ``None`` when no freq dict has the word, so the caller skips it cleanly."""
    from overlay.app.fsrs import diff_pill, harmonic_of

    ds = reader.dict_set
    sources = getattr(ds, "freqs", None)
    if not sources:
        return None
    # Only rank-based dicts may be blended — an occurrence-based dict's converted rank is a per-corpus
    # dense rank on an incomparable scale (see FreqSource.occurrence_based); it stays in the per-dict
    # pill row but never in the harmonic mean.
    forms = (tok.lemma, tok.surface, tok.reading)
    ranks = [
        r
        for fs in sources
        if not getattr(fs, "occurrence_based", False)
        and (r := fs.rank(forms, tok.reading)) is not None
    ]
    return diff_pill(harmonic_of([float(r) for r in ranks]))


def entry_for_tok(reader: Reader, tok, inflected, *, extra_terms: tuple[str, ...] = ()):
    """Look up the panel entry and fold in the blended-rareness pill and the JLPT pill (leading the
    frequency pills) when the word has them, so they mirror the subtitle underline / freq row.
    ``extra_terms`` are longer multi-token phrases starting at this word (数ある over 数); the dict set
    stacks them above the bare word.

    Never mutates the lru_cached Entry from lookup.lookup_entry / dict_set.entry_for — returns
    a shallow copy with a new freqs list so repeated calls do not accumulate pills."""
    if reader.dict_set is None:
        entry = entry_for(tok)
    elif extra_terms:  # only the phrase path needs the expanded lookup
        entry = reader.dict_set.entry_for(tok, inflected=inflected, extra_terms=extra_terms)
    else:
        entry = reader.dict_set.entry_for(tok, inflected)
    extra = [p for p in (rareness_pill(reader, tok), jlpt_pill(reader, tok)) if p is not None]
    if extra and hasattr(entry, "freqs"):
        # Build the pill list into a shallow copy — never mutate the cached original.
        entry = _dc.replace(entry, freqs=[*extra, *entry.freqs])
    return entry


def _build_panel(
    reader: Reader,
    _key: PanelKey,
    tok,
    inflected,
    *,
    mined: bool,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
) -> Panel:
    if otel_metrics.panel_cache_misses is not None:
        otel_metrics.panel_cache_misses.add(1)
    # kind is the base/nested IDENTITY. during_scroll flags a render triggered by the scan-hit-test
    # recomputing which cell is under a STATIONARY cursor after content moved under it (a nested popup
    # opening as a side effect of scrolling the base tooltip in the same poll tick), not a mouse move.
    with otel_metrics.instrumented(
        otel_metrics.render_duration_ms,
        "render",
        kind="nested" if nested else "base",
        during_scroll="1" if reader._scrolled_this_tick else "0",
        layout_backend=reader.layout_engine,
    ):
        # The base tooltip stacks the hovered word's longer phrase terms (passed in); nested popups
        # (inner scanned words) and prefetch pass none and look up the bare word only.
        entry = entry_for_tok(reader=reader, tok=tok, inflected=inflected, extra_terms=extra_terms)
        return Panel.from_rows(
            panel_rows(
                entry,
                reader.tip_width,
                add_button=anki_ok(reader),
                mined=mined,
                speak_button=reader._tts_ok,
                group_mined=_key.group_mined,
            ),
            reader.tip_width,
            getattr(entry, "reading", "") or tok.reading,
            band_cache_max=reader.band_cache_max,
            raw_band_ceiling=reader.raw_band_ceiling,
            layout_backend=reader.layout_backend,
        )


def _panel_cache_get(
    reader: Reader,
    key: PanelKey,
    tok,
    inflected,
    *,
    mined: bool,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
) -> Panel:
    st = reader._panel_cache.get(key)
    if st is None:
        st = _build_panel(
            reader,
            key,
            tok,
            inflected,
            mined=mined,
            nested=nested,
            extra_terms=extra_terms,
        )
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
    *,
    mined: bool | None = None,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
) -> Panel:
    """The memoised :class:`Panel` for a token: warm + measure the head that fills ``min_h`` px now;
    the windowed engine composites the rest on scroll. Re-hovering is instant and scrolling is cheap.
    ``mined`` (default: look it up) selects the ⊕ vs ✓ header variant and is part of the cache key.
    ``nested`` is the base/nested IDENTITY (drives the perf ``kind`` label); a nested popup passes
    ``nested=True``.

    Thread-safe: the panel is *built* lock-free (thread-local DB conns + fonts, each render owns its
    images), and only the tiny cache write/LRU update is locked. On a free-threaded (no-GIL) build,
    OrderedDict.get() is NOT atomic, so cache hits also acquire the lock briefly to move_to_end.
    Hovers remain snappy because the lock is held for only a few microseconds (no rendering inside)."""
    if mined is None:
        mined = is_mined(reader, tok)
    key = panel_key(reader, tok, inflected, mined=mined, phrase=extra_terms)
    st = _panel_cache_get(
        reader, key, tok, inflected, mined=mined, nested=nested, extra_terms=extra_terms
    )
    # The head walk+wrap (offset measure for placement) — runs on every hover, cold or warm, and was
    # the untraced bulk of tooltip_show's self-time (#158 territory). Cheap on a re-measured cached
    # panel, a full walk on a fresh one. Nests under tooltip_show / prefetch_decode.
    with otel_metrics.traced("measure"):
        st.render_head(min_h if min_h is not None else reader._tip_cap())
    return st


def panel_cache_setdefault(reader: Reader, key: PanelKey, st: Panel) -> Panel:
    """Insert ``st`` for ``key`` if not already present; evict the LRU entry when over the cap.
    Must be called under ``reader._cache_lock``. First-writer-wins: if two workers race to build
    the same panel, the winner's result is kept and the loser is discarded (both are equivalent)."""
    if key in reader._panel_cache:
        reader._panel_cache.move_to_end(key)
        return reader._panel_cache[key]
    # Evict least-recently-used entries until we are at the limit.
    while len(reader._panel_cache) >= reader.panel_cache_max:
        reader._panel_cache.popitem(last=False)  # FIFO/LRU: oldest (first) entry out
        if otel_metrics.panel_cache_evictions is not None:
            otel_metrics.panel_cache_evictions.add(1)
    reader._panel_cache[key] = st
    return st


# --- showing / placing / rendering the base tooltip ---------------------------------------------


def show_tooltip(reader: Reader, index: int) -> None:
    # "tooltip_show" is the end-to-end hover→drawn span (symmetric with scroll_frame/sub_seek); the
    # perf ring buffer stays for doctor/crashlog. Metrics recorded outside the spans so the kind
    # label (cold vs warm) — only known after impl builds/hits the panel — can split the histogram.
    start = time.perf_counter()
    with (
        otel_metrics.traced("tooltip_show", layout_backend=reader.layout_engine) as span,
        timed("show_tooltip"),
    ):
        show_tooltip_impl(reader, index)
        # Attribute a slow (usually cold) hover: whether it was a panel build vs a cache hit, the word
        # length + panel height (a tall multi-dict entry is the coldest), and bands rastered on the
        # first paint. All low-cardinality — no raw word surface. Sort spans by dur → read the why.
        st = reader._tip_state
        span.set("cold", reader._tip_show_cold)
        span.set("chars", len(reader.tokens[index].surface))
        if st is not None:
            span.set("full_h", st.full_height)
            span.set("bands", st.last_frame_rasters)
    _record_show_metrics(reader, (time.perf_counter() - start) * 1000.0)


def _record_show_metrics(reader: Reader, elapsed_ms: float) -> None:
    """Live percentiles + the cold-first-paint overshoot count for one base-tooltip show. The
    overshoot counter fires only on a COLD show past the budget — a warm cache-hit show over budget
    isn't a first-paint miss, so it must not pollute the signal viewport-first rendering is judged by."""
    cold = reader._tip_show_cold
    if otel_metrics.show_tooltip_duration_ms is not None:
        otel_metrics.show_tooltip_duration_ms.record(
            elapsed_ms, {"kind": "cold" if cold else "warm"}
        )
    if (
        cold
        and elapsed_ms > otel_metrics.COLD_FIRST_PAINT_BUDGET_MS
        and otel_metrics.cold_first_paint_overshoot is not None
    ):
        otel_metrics.cold_first_paint_overshoot.add(1)


def show_tooltip_impl(reader: Reader, index: int) -> None:
    reader._hide_nested()  # switching the base word drops any stale scan popup
    reader._tip_nav = []  # a newly hovered word abandons any link-navigation back-history
    reader._kanji_index = 0  # a new word restarts the `k` kanji cycle
    tok = reader.tokens[index]
    inflected = reader._inflected_surface(index)
    cap = reader._tip_cap()
    # Viewport-first: warm + measure only the head that fills the viewport now (placement); the
    # windowed engine composites the rest on scroll with overscan look-ahead.
    # jamdict card_for on the main thread (not worker-safe) — untraced until now; a suspect for the
    # tooltip_show self-time under --mine, where reader._mined is populated so this actually looks up.
    with otel_metrics.traced("mined"):
        mined = is_mined(reader, tok)
    phrase = reader._hover_terms
    key = panel_key(reader, tok, inflected, mined=mined, phrase=phrase)
    reader._tip_show_cold = key not in reader._panel_cache  # cold = a panel build, not a cache hit
    ox, oy = reader.sub_origin
    b = reader.boxes[index]
    wx, wy = ox + b.x, oy + b.y

    # Direct paint (#149): a COLD pathological hover the persistent cache has → place by the cached
    # full_h + decorate + upload the cached pixels NOW, skipping the whole build+measure+raster pipeline
    # so the user sees the tooltip in ~upload-time. The real interactive Panel is built right after (its
    # pixels are identical), off this paint's critical path — the reaction-latency window covers it.
    painted = _paint_from_cache(reader, key, cap, wx, wy, b.h) if reader._tip_show_cold else False

    st = panel_for(reader, tok, inflected, min_h=cap, mined=mined, extra_terms=phrase)
    # Seed the real panel's first viewport from disk too, so scrolling back to 0 later re-blits warm.
    # (Also the fallback fast path when direct-paint was skipped — cache miss on this key's geometry.)
    if reader._tip_show_cold and st.windowed.first_view is None:
        reader._seed_precomposed(st, key, cap)
    reader._tip_state, reader._tip_key = st, key
    reader._hover_reading = st.reading
    log.debug(
        "tooltip shown: word=%r phrases=%r reading=%r mined=%s painted_from_cache=%s",
        tok.surface,
        list(phrase),
        st.reading,
        mined,
        painted,
    )

    if not painted:
        reader._tip_scroll = 0
        # Safe area: keep clear of the OSC/window header at the top and the controls/edge at the bottom,
        # so the tooltip never spills under the window chrome. It scrolls, so we cap the height rather
        # than trying to fit the whole (very tall) entry. full_height is the windowed engine's estimate,
        # exact once the head measured the whole panel (short entry), converging otherwise.
        reader._tip_view_h = min(st.full_height, cap)
        reader._tip_xy = place_panel(reader, st.width, wx, wy, b.h, reader._tip_view_h)
        render_tip_view(reader)
    reader._bind_tip_keys()  # UP/DOWN/ESC live only while the tip shows
    # Pause-on-hover IPC: a _prop("pause") round-trip every hover + a set_property when it pauses —
    # two synchronous mpv round-trips, the untraced remainder of tooltip_show's self-time (the trace
    # showed ~876 round-trips at ~5ms). Its own span so that IPC cost stops hiding inside the parent.
    with otel_metrics.traced("pause_ipc"):
        if reader.pause_on_tooltip and not reader._paused_by_tip and not reader._prop("pause"):
            reader.ipc.command("set_property", "pause", True)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format; freeze the frame while you read
            reader._paused_by_tip = True
    # One panel: the blit above already composited native (crisp) at hi-dpi; the scroll-ahead worker
    # warms upcoming native bands. Keep the source token for the scroll path's warm requests.
    reader._tip_tok, reader._tip_inflected = tok, inflected


def place_panel(
    reader: Reader, full_w: int, wx: float, wy: float, wh: float, view_h: int
) -> tuple[int, int]:
    """Choose a top-left (tx, ty) for a panel of REFERENCE size ``full_w`` × ``view_h`` anchored to an
    on-screen word box (wx, wy, wh): above it if there's room, else below, clamped to the safe area. The
    panel is composited at reference size then upscaled by ``_tip_display_scale`` at upload, so placement
    uses the DISPLAYED size. Shared by the base tooltip and nested popups."""
    s = reader._tip_display_scale
    disp_w, disp_h = full_w * s, view_h * s
    margin = max(16, round(reader.osd[1] * 0.05))
    above_room = wy - TIP_GAP - margin
    below_room = (reader.osd[1] - margin) - (wy + wh + TIP_GAP)
    if above_room >= disp_h or above_room >= below_room:
        ty = wy - TIP_GAP - disp_h  # above the word
    else:
        ty = wy + wh + TIP_GAP  # below the word
    tx = max(margin, min(wx, reader.osd[0] - disp_w - margin))
    ty = max(margin, min(ty, reader.osd[1] - margin - disp_h))
    return int(tx), int(ty)


def _paint_from_cache(reader: Reader, key, cap: int, wx: float, wy: float, wh: float) -> bool:
    """Paint a cold hover DIRECTLY from the persistent render cache (#149): place by the cached ``full_h``
    and decorate + upload the cached premul-BGRA first viewport, skipping the entire build+measure+raster
    pipeline. Sets ``_tip_xy``/``_tip_view_h``/``_tip_scroll``/``_tip_rect`` so the real Panel built right
    after slots in without a re-blit. ``True`` when it painted (the caller then skips ``render_tip_view``).

    The array is copied because ``decorate_and_upload`` mutates it in place (scrollbar/flash) and the
    disk-backed buffer is read-only. Same content ⇒ the real panel's geometry matches this placement."""
    loaded = reader._peek_render_cache(key)
    if loaded is None:
        if otel_metrics.render_cache_misses is not None:
            otel_metrics.render_cache_misses.add(
                1
            )  # cold hover, nothing cached → full build follows
        return False
    if otel_metrics.render_cache_hits is not None:
        otel_metrics.render_cache_hits.add(1)  # cold hover served straight from disk (the #149 win)
    full_h = loaded.full_h
    reader._tip_scroll = 0
    reader._tip_view_h = min(full_h, cap)
    xy = place_panel(reader, loaded.array.shape[1], wx, wy, wh, reader._tip_view_h)
    reader._tip_xy = xy
    with otel_metrics.traced("tip_compose", cached="1"):
        view = loaded.array.copy()
    reader._tip_rect = decorate_and_upload(reader, view, 0, full_h, xy, OverlayId.TIP)
    return True


def blit_panel(reader: Reader, panel: Panel, scroll: int, view_h: int, xy, oid: int):
    """Composite the ``[y0, y0+vh)`` viewport from the windowed engine (O(viewport)) and decorate +
    upload it. ``overscan`` renders one viewport of blocks BELOW the fold and keeps them warm, so the
    next wheel notch composites without a hot-path render. The sole popup blit — base and nested."""
    full_h = panel.full_height
    vh = min(view_h, full_h)
    y0 = max(0, min(scroll, max(0, full_h - vh)))
    # The first-paint composite + any SYNCHRONOUS overscan band raster runs here on the calling
    # thread, was untraced, and is the bulk of tooltip_show's wall time that neither `render` (panel
    # build) nor `upload` (IPC) covered — a cold hover's tooltip_show read ~130ms of on-thread CPU
    # outside every child span until this span existed. Nests under tooltip_show / scroll_frame.
    # soft_reason/scale attribute WHY this blit is soft (vs crisp) + at what display scale, so a report
    # shows e.g. a run of `stale_scale` misses (the OSD scale jittered and orphaned the native panel).
    with otel_metrics.traced(
        "tip_compose",
        soft_reason=reader._crisp_miss or "n/a",
        scale=f"{reader._tip_display_scale:.4f}",
    ):
        view = panel.viewport(y0, vh, overscan=vh)  # exact BGRA viewport + one screen look-ahead
    return decorate_and_upload(reader, view, y0, full_h, xy, oid)


def decorate_and_upload(
    reader: Reader, view, y0: int, full_h: int, xy, oid: int, *, prescaled: bool = False
):
    """Draw the scrollbar thumb and the copy-flash border onto a REFERENCE-sized viewport BGRA array,
    then upscale by ``_tip_display_scale`` to the live display and upload. Decorations are drawn in
    reference px (crisp thumb) before the scale; the returned rect is in DISPLAY px (what the hit-test
    compares the OSD cursor against). ``prescaled`` (the idle crisp re-render) means ``view`` is ALREADY
    at display resolution, so the upscale is skipped — but ``full_h``/``y0`` are still display px so the
    thumb geometry stays right."""
    vh, full_w = view.shape[0], view.shape[1]
    if full_h > vh:  # scrollbar thumb (premultiplied BGRA gray)
        track = vh - 8
        th = max(28, int(track * vh / full_h))
        tyb = 4 + int((track - th) * (y0 / max(1, full_h - vh)))
        view[tyb : tyb + th, full_w - 7 : full_w - 3] = (99, 99, 99, 210)
    if reader._flash_oid == oid and time.monotonic() < reader._flash_until:
        b = 4  # "copied" highlight border (a brief visual pulse)
        view[:b, :] = view[-b:, :] = FLASH_BGRA
        view[:, :b] = view[:, -b:] = FLASH_BGRA
    s = reader._tip_display_scale
    if not prescaled and abs(s - 1.0) > 1e-3:  # only hi-dpi pays the resize; 1080p is a 1:1 no-op
        from overlay.bgra import scale_bgra

        view = scale_bgra(view, s)
    tx, ty = xy
    reader.ov.show_bgra(view, tx, ty, oid=oid)
    return (tx, ty, view.shape[1], view.shape[0])


_CRISP_MIN_SCALE = (
    1.05  # below this the soft upscale IS the native render (1080p ≈ 1.0) — no crisp pass
)


def hit_target(reader: Reader, *, nested: bool):
    """The ``(panel, scale, scroll)`` to hit-test a popup against — the ONE reference panel, always. It's
    composited natively (glyph masks over 1× geometry), so the DRAWN panel IS the hit-tested panel and the
    inverse is a single ``(mx-sx)/scale + scroll`` against 1× geometry — the two-geometry seam bug can't
    occur. ``scale`` is the BUCKETED raster scale the blit drew at, so hit-test == draw exactly."""
    if nested:
        ref, scroll = reader._nest.state, reader._nest.scroll
    else:
        ref, scroll = reader._tip_state, reader._tip_scroll
    return ref, reader._raster_scale, scroll


def render_tip_view(reader: Reader) -> None:
    """The base tooltip's SOLE blit path (SSOT): composite the CURRENT viewport CRISP straight from the
    cached native-scale panel when it's built (the common case once a word is shown — so scrolling stays
    crisp, no soft flash), else the soft reference upscale. Every re-blit — show, scroll, flash expiry,
    OSD change — routes through here, so nothing can flip a crisp viewport back to blurry."""
    st = reader._tip_state
    if st is None:
        return
    reader._tip_rect = _blit_crisp_or_soft(
        reader,
        st,
        reader._tip_key,
        reader._tip_scroll,
        reader._tip_view_h,
        reader._tip_xy,
        OverlayId.TIP,
    )


def _blit_native(reader: Reader, st: Panel, scroll: int, view_h: int, xy, oid: int):
    """One-panel (scale-boundary) blit: composite the ONE reference panel's viewport at the display scale
    — native crisp glyph masks over the 1× geometry — and upload 1:1. Soft below the crisp threshold
    (≈1080p, where native == the upscale). No second panel, no crisp cache: the drawn panel IS the
    reference panel, so it can't disagree with the hit-test (which reads the same 1× geometry)."""
    scale = (
        reader._raster_scale
    )  # bucketed → matches hit_target's inverse; reuses cached native bands
    if scale <= _CRISP_MIN_SCALE:  # 1080p — native == soft upscale, take the cheaper 1× path
        reader._crisp_miss = "not_hidpi"
        return blit_panel(reader, st, scroll, view_h, xy, oid)
    full_h = st.full_height
    vh = min(view_h, full_h)
    y0 = max(0, min(scroll, max(0, full_h - vh)))
    try:
        with otel_metrics.traced("tip_compose", soft_reason="native", scale=f"{scale:.4f}"):
            arr = st.viewport(y0, vh, overscan=vh, scale=scale)  # native BGRA over 1× geometry
    except Exception:  # a composite failure falls back to the soft upscale (never a blank tooltip)
        log.debug("native compose failed", exc_info=True)
        return blit_panel(reader, st, scroll, view_h, xy, oid)
    reader._crisp_miss = ""
    if otel_metrics.crisp_swaps is not None:
        otel_metrics.crisp_swaps.add(1)
    # y0/full_h are display px so decorate_and_upload's scrollbar-thumb geometry stays right; the array
    # is already native (prescaled) so no scale_bgra.
    return decorate_and_upload(
        reader, arr, round(y0 * scale), round(full_h * scale), xy, oid, prescaled=True
    )


def _blit_crisp_or_soft(reader: Reader, st: Panel, _key, scroll: int, view_h: int, xy, oid: int):
    """Composite ``[scroll, scroll+view_h)`` of popup ``st`` and return its display-px rect. One panel:
    the reference panel composites natively at the display scale (``_blit_native``), soft below the crisp
    threshold. ``_key`` is unused now (kept for the base/nested call signature). The SSOT both popups blit
    through, so each is crisp exactly when hi-dpi."""
    return _blit_native(reader, st, scroll, view_h, xy, oid)


def _capture_tip_view(reader: Reader) -> tuple:
    """Snapshot the base tooltip's renderable view for the link-navigation back-stack. Includes the
    source token so a restored HOVERED view can still re-request crisp band-warming on scroll."""
    return (
        reader._tip_state,
        reader._tip_key,
        reader._hover_reading,
        reader._tip_view_h,
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_tok,
        reader._tip_inflected,
    )


def _restore_tip_view(reader: Reader, view: tuple) -> None:
    (
        reader._tip_state,
        reader._tip_key,
        reader._hover_reading,
        reader._tip_view_h,
        reader._tip_xy,
        reader._tip_scroll,
        reader._tip_tok,
        reader._tip_inflected,
    ) = view


def _navigated_panel(reader: Reader, query: str) -> Panel | None:
    """The read-only reference Panel for a navigation target: a wildcard/prefix query → search results,
    else the exact term. No ⊕ — the header mine button acts on the hovered SUBTITLE word, which the
    navigated term is not, so mining stays on the base word (reachable via back). Built at 1× like every
    panel; the one-panel blit composites it natively at the display scale."""
    if reader.dict_set is None:
        return None
    if any(c in query for c in "*?＊？"):
        entry = reader.dict_set.search(query)
        reading = ""
    else:
        tok = query_token(
            query
        )  # look up the WHOLE query as one term — never tokenize a link target
        if tok is None:
            return None
        entry = entry_for_tok(reader, tok, tok.surface)
        reading = getattr(entry, "reading", "") or tok.reading
    rows = panel_rows(entry, reader.tip_width, add_button=False, speak_button=reader._tts_ok)
    return Panel.from_rows(
        rows,
        reader.tip_width,
        reading,
        band_cache_max=reader.band_cache_max,
        raw_band_ceiling=reader.raw_band_ceiling,
        layout_backend=reader.layout_backend,
    )


def navigate_tip(reader: Reader, query: str) -> None:
    """Replace the base tooltip's content with the entry for ``query`` (a clicked cross-reference),
    pushing the current view onto the back-stack (Esc/back returns). The popup stays put — same anchor,
    same TIP slot — so this reads as an in-place navigation, not a new floating popup."""
    if reader.dict_set is None:
        return
    st = _navigated_panel(reader, query)
    if st is None:
        return
    st.render_head(reader._tip_cap())  # warm the head so full_height sizes the viewport correctly
    reader._hide_nested()  # the old content's scan popup is stale
    reader._tip_nav.append(_capture_tip_view(reader))
    reader._tip_state = st
    # A navigated view is keyless (not a subtitle token) — the one panel composites native from its own
    # reference panel, so no synthetic key is needed. _tip_tok=None so scroll won't rebuild from a token.
    reader._tip_key = None
    reader._tip_tok = reader._tip_inflected = None
    reader._hover_reading = st.reading
    reader._tip_scroll = 0
    reader._tip_view_h = min(st.full_height, reader._tip_cap())
    render_tip_view(reader)


def tip_back(reader: Reader) -> bool:
    """Pop one link-navigation step, restoring the previous base view. Returns False when there is no
    history (a plain hovered word) so the caller falls through to closing the tooltip."""
    if not reader._tip_nav:
        return False
    _restore_tip_view(reader, reader._tip_nav.pop())
    render_tip_view(reader)
    return True


def scroll_tip(reader: Reader, delta: int) -> None:
    # route the wheel to whichever popup the cursor is over (nested sits on top)
    if reader._nest.rect is not None and reader._in_rect(reader._nest.rect, *reader._last_mouse):
        reader._scroll_nested(delta)
        return
    st = reader._tip_state
    if st is None:
        return
    # clamp to the windowed full height (converging estimate) — the head is only the first viewport,
    # so the windowed engine owns the true (growing) height.
    maxs = max(0, st.full_height - reader._tip_view_h)
    ns = min(maxs, max(0, reader._tip_scroll + delta))
    if ns != reader._tip_scroll:
        going = 1 if delta > 0 else -1
        reader._tip_scroll = ns
        reader._hide_at = 0.0  # scrolling counts as interacting → keep it up
        reader._scan_target = None  # content moved under the cursor → restart the scan dwell
        render_tip_view(reader)  # composites native (crisp) at hi-dpi, soft below the threshold
        # Warm the next NATIVE bands off the main thread (render_ahead uses the bucketed display scale),
        # so continued scrolling composites crisp without a synchronous raster.
        prefetch.request_render_ahead(reader, going)


# --- nested scanning: hover a word INSIDE the tooltip → its own popup ---------------------------


def scan_hit(reader: Reader, mx: float, my: float):
    """Which per-character scan cell of the base tooltip is under (mx, my)? Maps screen → panel
    coords (accounting for scroll) and returns the :class:`~overlay.model.ScanBox`, or None. Hit-tests the
    panel actually DRAWN (crisp native when shown, else reference) so a hover lands on the right cell."""
    if reader._tip_state is None or reader._tip_rect is None:
        return None
    panel, s, scroll = hit_target(reader, nested=False)  # the on-screen panel + its scale/scroll
    if panel is None:
        return None
    sx, sy = reader._tip_xy
    px = (mx - sx) / s
    py = (my - sy) / s + scroll
    return panel.windowed.scan_hit(
        int(px), int(py)
    )  # windowed hit-test (retained per-block geometry)
