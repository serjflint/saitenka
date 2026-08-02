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
from overlay.app.tokenize import phrase_terms
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
    if top < 0 or top + ph > view_h:  # header scrolled out of the viewport
        return False
    sx, sy = xy
    return reader._in_rect((sx + px, sy + top, pw, ph), x, y)


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
        lb = reader._link_hit(x, y, reader._nest.state, reader._nest.xy, reader._nest.scroll)
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
    lb = reader._link_hit(x, y, reader._tip_state, reader._tip_xy, reader._tip_scroll)
    if lb is not None:
        tok = reader.tokens[reader.hover] if 0 <= reader.hover < len(reader.tokens) else None
        if not _mine_link(reader, lb, tok):  # stacked entry ⊕ → mine that entry
            reader._open_link(lb, reader._tip_xy, reader._tip_scroll)  # cross-ref → nested popup
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
    # ``phrase`` is the base word's stacked multi-token terms (empty for nested/prefetch).
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
    with otel_metrics.traced("tooltip_show"), timed("show_tooltip"):
        show_tooltip_impl(reader, index)
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
    reader._kanji_index = 0  # a new word restarts the `k` kanji cycle
    tok = reader.tokens[index]
    inflected = reader._inflected_surface(index)
    cap = reader._tip_cap()
    # Viewport-first: warm + measure only the head that fills the viewport now (placement); the
    # windowed engine composites the rest on scroll with overscan look-ahead.
    mined = is_mined(reader, tok)
    phrase = reader._hover_terms
    key = panel_key(reader, tok, inflected, mined=mined, phrase=phrase)
    reader._tip_show_cold = key not in reader._panel_cache  # cold = a panel build, not a cache hit
    st = panel_for(reader, tok, inflected, min_h=cap, mined=mined, extra_terms=phrase)
    reader._tip_state, reader._tip_key = st, key
    reader._hover_reading = st.reading
    reader._tip_scroll = 0
    log.debug(
        "tooltip shown: word=%r phrases=%r reading=%r mined=%s",
        tok.surface,
        list(phrase),
        st.reading,
        mined,
    )

    ox, oy = reader.sub_origin
    b = reader.boxes[index]
    wx, wy = ox + b.x, oy + b.y
    # Safe area: keep clear of the OSC/window header at the top and the controls/edge at the bottom,
    # so the tooltip never spills under the window chrome. It scrolls, so we cap the height rather
    # than trying to fit the whole (very tall) entry. full_height is the windowed engine's estimate,
    # exact once the head measured the whole panel (short entry), converging otherwise.
    ph, pw = st.full_height, st.width
    reader._tip_view_h = min(ph, cap)
    reader._tip_xy = place_panel(reader, pw, wx, wy, b.h, reader._tip_view_h)
    render_tip_view(reader)
    reader._bind_tip_keys()  # UP/DOWN/ESC live only while the tip shows
    if reader.pause_on_tooltip and not reader._paused_by_tip and not reader._prop("pause"):
        reader.ipc.command("set_property", "pause", True)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format; freeze the frame while you read
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


def blit_panel(reader: Reader, panel: Panel, scroll: int, view_h: int, xy, oid: int):
    """Composite the ``[y0, y0+vh)`` viewport from the windowed engine (O(viewport)) and decorate +
    upload it. ``overscan`` renders one viewport of blocks BELOW the fold and keeps them warm, so the
    next wheel notch composites without a hot-path render. The sole popup blit — base and nested."""
    full_h = panel.full_height
    vh = min(view_h, full_h)
    y0 = max(0, min(scroll, max(0, full_h - vh)))
    view = panel.viewport(y0, vh, overscan=vh)  # exact BGRA viewport + one screen look-ahead
    return decorate_and_upload(reader, view, y0, full_h, xy, oid)


def decorate_and_upload(reader: Reader, view, y0: int, full_h: int, xy, oid: int):
    """Draw the scrollbar thumb and the copy-flash border onto a viewport-sized BGRA array, then
    upload it."""
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
    tx, ty = xy
    reader.ov.show_bgra(view, tx, ty, oid=oid)
    return (tx, ty, full_w, view.shape[0])


def render_tip_view(reader: Reader) -> None:
    st = reader._tip_state
    if st is None:
        return
    reader._tip_rect = blit_panel(
        reader, st, reader._tip_scroll, reader._tip_view_h, reader._tip_xy, OverlayId.TIP
    )


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
        render_tip_view(reader)
        prefetch.request_render_ahead(reader, going)  # warm the next blocks off the main thread


# --- nested scanning: hover a word INSIDE the tooltip → its own popup ---------------------------


def scan_hit(reader: Reader, mx: float, my: float):
    """Which per-character scan cell of the base tooltip is under (mx, my)? Maps screen → panel
    coords (accounting for scroll) and returns the :class:`~overlay.model.ScanBox`, or None."""
    st = reader._tip_state
    if st is None or reader._tip_rect is None:
        return None
    sx, sy = reader._tip_xy
    px = mx - sx
    py = (my - sy) + reader._tip_scroll
    return st.windowed.scan_hit(int(px), int(py))  # windowed hit-test (retained per-block geometry)
