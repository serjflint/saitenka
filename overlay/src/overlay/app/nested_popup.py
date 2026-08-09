"""The nested popup: hovering (or clicking a cross-reference link inside) a word INSIDE the base
tooltip opens a depth-1 "quick look" popup for that inner word, anchored above/below it — the
Yomitan-style scan-inside-scan. Also home to kanji-lookup mode (``k``) and wildcard/prefix search
results, both of which reuse the same nested-popup anchoring.

Takes ``reader: Reader`` (the AGENTS.md seam pattern); the nested popup's own state
(``reader._nest``, a :class:`~overlay.app.popups.PopupView`) stays on the Reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay.app.overlay_ids import OverlayId
from overlay.app.popups import Panel, PopupView
from overlay.app.prefetch import cap_for
from overlay.app.tokenize import SKIP_POS
from overlay.panel import panel_rows

if TYPE_CHECKING:
    from overlay.app.controller import Reader

TIP_GAP = 12
NEST_MIN_ABOVE = 140  # min room above an inner word to keep its nested popup above it (else below)


@dataclass(frozen=True)
class Anchor:
    """The on-screen box a nested popup anchors above/below — the hovered inner word / kanji / link."""

    wx: float
    wy: float
    wh: float


def _is_ideograph(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF


def nested_view_h(reader: Reader, full_h: int, wy: float) -> int:
    """Nested-popup viewport height, capped to the room ABOVE the hovered inner word (when that room
    is decent) so the popup stays above it and the text below the cursor — the definition and the
    subtitle sentence — remains readable (the popup scrolls, so capping loses nothing)."""
    margin = max(16, round(reader.osd[1] * 0.05))
    view_h = min(full_h, cap_for(reader, reader.nested_max_frac))
    above_room = int(wy) - TIP_GAP - margin
    if view_h > above_room >= NEST_MIN_ABOVE:
        view_h = above_room  # shrink to fit above rather than drop below
    return view_h


def render_nested_view(reader: Reader) -> None:
    # The nested popup is a compact depth-1 quick look, scrolled with the wheel — composited crisp from
    # its cached native panel (built by the crisp worker, or ahead of time by the lookahead) when hi-dpi,
    # else the soft reference upscale. Same SSOT blit path as the base tooltip, keyed on _nest.key.
    # Reached via the Reader seam (not a direct tooltip import — tooltip already imports this module for
    # TIP_GAP, so the reverse edge would cycle).
    st = reader._nest.state
    if st is None:
        return
    reader._nest.rect = reader._blit_crisp_or_soft(
        st,
        reader._nest.key,
        reader._nest.scroll,
        reader._nest.view_h,
        reader._nest.xy,
        OverlayId.NESTED,
    )


def scroll_nested(reader: Reader, delta: int) -> None:
    st = reader._nest.state
    if st is None:
        return
    maxs = max(
        0, st.full_height - reader._nest.view_h
    )  # windowed estimate, converging as it renders
    ns = min(maxs, max(0, reader._nest.scroll + delta))
    if ns != reader._nest.scroll:
        reader._nest.scroll = ns
        reader._nest.hide_at = 0.0
        render_nested_view(reader)


def show_nested(reader: Reader, sb) -> None:
    """Open (or switch) the nested popup for the word starting at scan cell ``sb`` — its text is the
    Yomitan-style tail from the hovered char, so the first token is the word under the cursor. The
    popup is anchored to that inner word's on-screen cell, above/below like the base tooltip."""
    tokens = reader.tokenizer.tokenize(sb.text)
    tok = tokens[0] if tokens else None
    if tok is None or tok.pos in SKIP_POS or not tok.surface.strip():
        hide_nested(reader)
        return
    if tok.surface == reader._nest.word and reader._nest.state is not None:
        reader._nest.tail = sb.text  # same word, new cell → don't re-scan it
        return
    # Longest-match, Yomitan-style: stack any multi-token dictionary term starting under the cursor
    # (コンサート over the over-split コン) — the same forward longest-match the base tooltip applies to a
    # hovered cue word, so an inner katakana/compound word opens whole instead of as its first morpheme.
    extra = _phrase_extra_terms(reader, tokens)
    sx, sy = reader._tip_xy  # anchor to the inner word's screen cell
    wx = sx + sb.x
    wy = sy + (sb.y - reader._tip_scroll)
    open_nested(reader, tok, tok.surface, wx, wy, sb.h, tail=sb.text, extra_terms=extra)


def _phrase_extra_terms(reader: Reader, tokens) -> tuple[str, ...]:
    """Longest-first multi-token dict terms starting at the scanned tail's first token (index 0), via
    the same ``phrase_terms`` seam the base tooltip uses. Empty when the dict set has no phrase probe."""
    has_term = getattr(reader.dict_set, "has_term", None)
    if has_term is None:
        return ()
    got = reader.tokenizer.phrase_terms(tokens=tokens, index=0, has_term=has_term)
    return tuple(got[0]) if got is not None else ()


def open_nested(
    reader: Reader,
    tok,
    inflected,
    wx: float,
    wy: float,
    wh: float,
    tail=None,
    extra_terms: tuple[str, ...] = (),
) -> None:
    """Build the nested popup for ``tok`` and anchor it above/below an on-screen box (wx, wy, wh).
    Shared by scan-hover and a clicked cross-reference link. ``extra_terms`` are the longest-match
    phrases stacked above the bare word (empty for a clicked link, whose query is already exact)."""
    mined = reader._is_mined(tok)
    key = reader._panel_key(tok, inflected, mined=mined, phrase=extra_terms)
    st = reader._panel_for(
        tok, inflected, min_h=reader._tip_cap(), mined=mined, nested=True, extra_terms=extra_terms
    )
    place_nested(reader, st, key, tok, tok.surface, Anchor(wx, wy, wh), tail)


def place_nested(reader: Reader, st, key, token, word: str, anchor: Anchor, tail=None) -> None:
    """Anchor a built :class:`Panel` ``st`` as the nested popup. ``token`` is the inner Token to mine
    via its ⊕ (None for a wildcard-search results popup, whose rows aren't a single word)."""
    reader._nest.state, reader._nest.key = st, key
    reader._nest.token, reader._nest.word = token, word
    reader._nest.tail = tail
    reader._nest.scroll = 0
    reader._nest.view_h = nested_view_h(reader, st.full_height, anchor.wy)
    reader._nest.xy = reader._place_panel(
        st.width, anchor.wx, anchor.wy, anchor.wh, reader._nest.view_h
    )
    render_nested_view(reader)


def link_hit(mx: float, my: float, state, xy, scroll: int, *, scale: float = 1.0):
    """The :class:`~overlay.model.LinkBox` of ``state`` under (mx, my), via the windowed hit-test.
    ``scale`` is the reference→display factor (``_tip_display_scale``): the panel is composited at the
    reference size then upscaled to the display, so the screen offset is divided back to panel px."""
    if state is None:
        return None
    sx, sy = xy
    return state.windowed.link_hit(int((mx - sx) / scale), int((my - sy) / scale + scroll))


def open_link(reader: Reader, lb, xy, scroll: int) -> None:
    """A cross-reference link was clicked → open its target in the nested popup (navigating it if
    the click came from a nested popup). A wildcard target (``*``/``?``) opens a search-results
    popup whose rows are themselves clickable links back into exact terms."""
    q = lb.query
    sx, sy = xy
    wx, wy = sx + lb.x, sy + (lb.y - scroll)
    if reader.dict_set is not None and any(c in q for c in "*?＊？"):
        open_search(reader, q, wx, wy, lb.h)
        return
    # look up the WHOLE query as one term — never tokenize a link target
    tok = reader.tokenizer.query_token(q)
    if tok is None:
        return
    open_nested(reader, tok, tok.surface, wx, wy, lb.h, tail=None)


def open_search(reader: Reader, pattern: str, wx: float, wy: float, wh: float) -> None:
    """Open a wildcard/prefix search-results popup for ``pattern``."""
    if reader.dict_set is None:
        return
    key = ("search", pattern, reader.tip_width)
    st = reader._panel_cache.get(key)
    if st is None:
        entry = reader.dict_set.search(pattern)
        st = Panel.from_rows(
            panel_rows(entry, reader.tip_width, add_button=False, speak_button=reader._tts_ok),
            reader.tip_width,
            "",
            band_cache_max=reader.band_cache_max,
            raw_band_ceiling=reader.raw_band_ceiling,
            layout_backend=reader.layout_backend,
        )
        with reader._cache_lock:
            st = reader._panel_cache_setdefault(key, st)
    else:
        with reader._cache_lock:
            try:
                reader._panel_cache.move_to_end(key)
            except KeyError:
                pass
    st.render_head(reader._tip_cap())
    place_nested(reader, st, key, None, pattern, Anchor(wx, wy, wh))


def kanji_current(reader: Reader) -> None:
    """`k` — open the hovered word's first kanji in the nested popup; repeat cycles through
    the word's kanji."""
    if reader.dict_set is None or not (0 <= reader.hover < len(reader.tokens)):
        return
    chars = [c for c in reader.tokens[reader.hover].surface if _is_ideograph(c)]
    if not chars:
        reader._toast("no kanji in this word", "warn", 1.2)
        return
    ch = chars[reader._kanji_index % len(chars)]
    reader._kanji_index += 1
    ox, oy = reader.sub_origin
    b = reader.boxes[reader.hover]
    open_kanji(reader, ch, ox + b.x, oy + b.y, b.h)


def open_kanji(reader: Reader, ch: str, wx: float, wy: float, wh: float) -> None:
    """Open the kanji entry for ``ch`` in the nested popup (normal panel path, no raster code)."""
    assert reader.dict_set is not None
    entry = reader.dict_set.kanji_for(ch)
    if entry is None:
        reader._toast(f"no kanji entry for {ch}", "warn", 1.2)
        return
    key = ("kanji", ch, reader.tip_width)
    st = reader._panel_cache.get(key)
    if st is None:
        st = Panel.from_rows(
            panel_rows(entry, reader.tip_width, speak_button=reader._tts_ok),
            reader.tip_width,
            entry.reading,
            band_cache_max=reader.band_cache_max,
            raw_band_ceiling=reader.raw_band_ceiling,
            layout_backend=reader.layout_backend,
        )
        with reader._cache_lock:
            st = reader._panel_cache_setdefault(key, st)
    else:
        with reader._cache_lock:
            try:
                reader._panel_cache.move_to_end(key)
            except KeyError:
                pass
    st.render_head(reader._tip_cap())
    place_nested(reader, st, key, None, ch, Anchor(wx, wy, wh))


def click_kanji_fallback(reader: Reader, x: float, y: float) -> None:
    """A click on a SINGLE-ideograph scan cell whose token has no term match opens the kanji
    entry instead — reuses the nested-popup route."""
    if reader.dict_set is None:
        return
    sb = reader._scan_hit(x, y)
    if sb is None or not sb.text:
        return
    ch = sb.text[0]
    if not _is_ideograph(ch):
        return
    toks = reader.tokenizer.tokenize(sb.text)
    tok = toks[0] if toks else None
    if (
        tok is not None
        and len(tok.surface) == 1
        and not reader.dict_set.has_term(tok.lemma, tok.surface, tok.reading)
    ):
        sx, sy = reader._tip_xy
        open_kanji(reader, ch, sx + sb.x, sy + (sb.y - reader._tip_scroll), sb.h)


def hide_nested(reader: Reader) -> None:
    if reader._nest.state is not None or reader._nest.rect is not None:
        reader.ov.hide(OverlayId.NESTED)
    reader._nest = PopupView()
