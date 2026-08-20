"""The nested popup: hovering (or clicking a cross-reference link inside) a word INSIDE the base
tooltip opens a depth-1 "quick look" popup for that inner word, anchored above/below it — the
Yomitan-style scan-inside-scan. Also home to kanji-lookup mode (``k``) and wildcard/prefix search
results, both of which reuse the same nested-popup anchoring.

Takes ``reader: Reader`` (the AGENTS.md seam pattern); the nested popup's own state
(``reader._nest``, a :class:`~saitenka.app.popups.PopupView`) stays on the Reader.

Builds and blits through :mod:`saitenka.app.tooltip_panel` — the leaf importing the panel
machinery, which is the direction that does not cycle. Every one of those calls used to go back
out through a one-line ``Reader`` delegation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app import tooltip_engaged
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.popups import Panel, PopupView
from saitenka.app.prefetch import cap_for
from saitenka.app.subtitles import box_for_token
from saitenka.app.tooltip_panel import (
    TIP_GAP,
    is_mined,
    panel_for,
    panel_key,
    place_panel,
    render_view,
    scan_hit,
)
from saitenka.model import is_ideograph
from saitenka.panel import panel_rows

if TYPE_CHECKING:
    from saitenka.app.controller import Reader

NEST_MIN_ABOVE = 140  # min room above an inner word to keep its nested popup above it (else below)


@dataclass(frozen=True)
class Anchor:
    """The on-screen box a nested popup anchors above/below — the hovered inner word / kanji / link."""

    wx: float
    wy: float
    wh: float


def nested_view_h(full_h: int, wy: float, *, osd_h: int, max_frac: float) -> int:
    """Nested-popup viewport height, capped to the room ABOVE the hovered inner word (when that room
    is decent) so the popup stays above it and the text below the cursor — the definition and the
    subtitle sentence — remains readable (the popup scrolls, so capping loses nothing)."""
    margin = max(16, round(osd_h * 0.05))
    view_h = min(full_h, cap_for(max_frac))
    above_room = int(wy) - TIP_GAP - margin
    if view_h > above_room >= NEST_MIN_ABOVE:
        view_h = above_room  # shrink to fit above rather than drop below
    return view_h


def show_nested(reader: Reader, sb) -> None:
    """Open (or switch) the nested popup for the word starting at scan cell ``sb`` — its text is the
    Yomitan-style tail from the hovered char, so the first token is the word under the cursor. The
    popup is anchored to that inner word's on-screen cell, above/below like the base tooltip."""
    if reader._interaction_metadata_submit is not None:
        from saitenka.app.hover_metadata import NestedMetadataKey, NestedMetadataRequest

        reader._request_interaction_metadata(
            NestedMetadataRequest(
                NestedMetadataKey(
                    reader._prefetch_gen,
                    reader._dependency_generation,
                    reader._mined.generation,
                    id(reader._tip_state),
                    sb.text,
                ),
                reader.tokenizer.name,
                reader.dict_set,
                reader._mined.snapshot(),
            )
        )
        return
    tokens = reader.tokenizer.tokenize(sb.text)
    tok = tokens[0] if tokens else None
    if tok is None or reader.tokenizer.is_skippable(tok):
        hide_nested(reader)
        return
    if tok.surface == reader._nest.word and reader._nest.state is not None:
        reader._nest.tail = sb.text  # same word, new cell → don't re-scan it
        return
    # Longest-match, Yomitan-style: stack any multi-token dictionary term starting under the cursor
    # (コンサート over the over-split コン) — the same forward longest-match the base tooltip applies to a
    # hovered cue word, so an inner katakana/compound word opens whole instead of as its first morpheme.
    extra = _phrase_extra_terms(tokens, dict_set=reader.dict_set, tokenizer=reader.tokenizer)
    sx, sy = reader._tip_xy  # anchor to the inner word's screen cell
    anchor = Anchor(sx + sb.x, sy + (sb.y - reader._tip_scroll), sb.h)
    # defer=True: a cold inner word's head+bands raster off the main thread (tier-3), re-derived from the
    # scan cell when it lands — the hover-scan path, unlike a clicked link, is re-derivable via scan_hit.
    open_nested(reader, tok, tok.surface, anchor, tail=sb.text, extra_terms=extra, defer=True)


def apply_nested_metadata(reader: Reader, result) -> None:
    key = result.key
    if (
        result.error
        or result.token is None
        or key.generation != reader._prefetch_gen
        or key.dependency_generation != reader._dependency_generation
        or key.mined_generation != reader._mined.generation
        or key.tooltip_origin != id(reader._tip_state)
    ):
        return
    sb = scan_hit(reader.tip, reader._raster_scale, *reader._last_mouse)
    if sb is None or sb.text != key.tail:
        return
    sx, sy = reader._tip_xy
    anchor = Anchor(sx + sb.x, sy + (sb.y - reader._tip_scroll), sb.h)
    open_nested(
        reader,
        result.token,
        result.token.surface,
        anchor,
        tail=key.tail,
        extra_terms=result.phrase_terms,
        mined=result.mined,
        group_mined=result.group_mined,
        defer=True,
    )


def _phrase_extra_terms(tokens, *, dict_set, tokenizer) -> tuple[str, ...]:
    """Longest-first multi-token dict terms starting at the scanned tail's first token (index 0), via
    the same ``phrase_terms`` seam the base tooltip uses. Empty when the dict set has no phrase probe."""
    has_term = getattr(dict_set, "has_term", None)
    if has_term is None:
        return ()
    got = tokenizer.phrase_terms(tokens=tokens, index=0, has_term=has_term)
    return tuple(got[0]) if got is not None else ()


def open_nested(  # noqa: PLR0913 -- identity-qualified prepared metadata crosses this seam
    reader: Reader,
    tok,
    inflected,
    anchor: Anchor,
    tail=None,
    extra_terms: tuple[str, ...] = (),
    *,
    defer: bool = False,
    mined: bool | None = None,
    group_mined: tuple[bool, ...] | None = None,
) -> None:
    """Build the nested popup for ``tok`` and anchor it above/below the on-screen box ``anchor``. Shared
    by scan-hover and a clicked cross-reference link. ``extra_terms`` are the longest-match phrases
    stacked above the bare word (empty for a clicked link, whose query is already exact).

    ``defer`` (the scan-hover path only): on a cold inner word, request a bounded off-thread compose and
    show NOTHING — its typed completion re-derives the
    anchor from the scan cell and re-opens warm, keeping the getmask2 raster off the hover tick (#293). A
    clicked link is NOT re-derivable via scan_hit, so it never defers (builds synchronously below)."""
    if mined is None:
        mined = is_mined(tok, reader._mined)
    key = panel_key(
        reader,
        tok,
        inflected,
        mined=mined,
        phrase=extra_terms,
        group_mined=group_mined,
    )
    if defer and key not in reader._panel_cache:
        # Retain the identity-qualified presentation inputs while the engaged worker warms this key.
        reader._nest.key = key
        reader._nest.token = tok
        reader._nest.word = tok.surface
        reader._nest.tail = tail or tok.surface
        request = tooltip_engaged.HoverRequest(
            tok,
            inflected,
            mined,
            tuple(key),
            reader._tip_cap(),
            tuple(extra_terms),
            nested=True,
            tail=tail or tok.surface,
            job_id=reader._nest.job_id,
        )
        if reader._request_engaged_tooltip(request):
            return
    st = panel_for(
        reader,
        tok,
        inflected,
        min_h=reader._tip_cap(),
        mined=mined,
        nested=True,
        extra_terms=extra_terms,
        group_mined=group_mined,
    )
    place_nested(reader, st, key, tok, tok.surface, anchor, tail)


def place_nested(reader: Reader, st, key, token, word: str, anchor: Anchor, tail=None) -> None:
    """Anchor a built :class:`Panel` ``st`` as the nested popup. ``token`` is the inner Token to mine
    via its ⊕ (None for a wildcard-search results popup, whose rows aren't a single word)."""
    reader._nest.state, reader._nest.key = st, key
    reader._nest.token, reader._nest.word = token, word
    reader._nest.tail = tail
    reader._nest.scroll = 0
    reader._nest.desired_scroll = 0
    reader._nest.view_h = nested_view_h(
        st.full_height, anchor.wy, osd_h=reader.osd[1], max_frac=reader.nested_max_frac
    )
    reader._nest.xy = place_panel(
        st.width,
        anchor.wx,
        anchor.wy,
        anchor.wh,
        reader._nest.view_h,
        scale=reader._tip_display_scale,
        osd=reader.osd,
    )
    # Kick a render-ahead so a first wheel notch on the nested popup composites crisp off warm
    # bands, like the base tooltip.
    render_view(reader, reader._nest)
    reader._request_render_ahead(reader._nest, 1)


def rerender_with_mined_state(reader: Reader) -> None:
    """Rebuild the nested popup in place with the current mined-state, keeping its position."""
    tok = reader._nest.token
    if tok is None:
        return
    mined = is_mined(tok, reader._mined)
    st = panel_for(reader, tok, tok.surface, min_h=reader._tip_cap(), mined=mined)
    reader._nest.state = st
    reader._nest.key = panel_key(reader, tok, tok.surface, mined=mined)
    render_view(reader, reader._nest)


def link_hit(mx: float, my: float, state, xy, scroll: int, *, scale: float = 1.0):
    """The :class:`~saitenka.model.LinkBox` of ``state`` under (mx, my), via the windowed hit-test.
    ``scale`` is the reference→display factor (``_tip_display_scale``): the panel is composited at the
    reference size then upscaled to the display, so the screen offset is divided back to panel px."""
    if state is None:
        return None
    sx, sy = xy
    return state.windowed.link_hit(int((mx - sx) / scale), int((my - sy) / scale + scroll))


def _cached_rows_panel(reader: Reader, key, entry, reading: str) -> Panel:
    """Fetch-or-build (and LRU-touch) the ``_panel_cache`` entry for a non-token popup (kanji / search),
    measuring its head. Idempotent — the main-thread build, the worker warm, and the tick re-show all
    land on the same cached Panel."""
    st = reader._panel_cache.get_or_build(
        key,
        lambda: Panel.from_rows(
            panel_rows(entry, reader.tip_width, add_button=False, speak_button=reader._tts_ok),
            reader.tip_width,
            reading,
            band_cache_max=reader.band_cache_max,
            raw_band_ceiling=reader.raw_band_ceiling,
            layout_backend=reader.layout_backend,
        ),
    )
    st.render_head(reader._tip_cap())
    return st


def _engaged_open_panel(reader: Reader, source: str, query: str, *, mined: bool | None = None):
    """Build (or fetch cached) + measure the panel for a clicked/keyed nested open, WITHOUT placing it.
    Shared by the main-thread open (existence check + defer decision), the worker (warm the bands
    off-thread), and the tick (warm cache-hit re-show). Returns ``(panel, key, token, word, mined)`` or
    ``None`` (no entry — the caller toasts). ``source`` ∈ {``kanji``, ``search``, ``link``}. ``mined`` is
    forced by the worker (which must NOT touch jamdict via ``is_mined``); ``None`` = compute it here
    (main thread only — the tick recomputes for the freshest ⊕/✓)."""
    ds = reader.dict_set
    if ds is None:
        return None
    key: tuple  # a ("kanji"/"search", …) tuple or a PanelKey (a NamedTuple) — both are tuples
    if source == "kanji":
        entry = ds.kanji_for(query, stroke_order=reader.kanji_stroke_order)
        if entry is None:
            return None
        key = ("kanji", query, reader.tip_width)
        return _cached_rows_panel(reader, key, entry, entry.reading), key, None, query, False
    if source == "search":
        key = ("search", query, reader.tip_width)
        return _cached_rows_panel(reader, key, ds.search(query), ""), key, None, query, False
    # link → the WHOLE query as one exact term (never tokenize a link target); minable inner word
    tok = reader.tokenizer.query_token(query)
    if tok is None:
        return None
    if mined is None:  # main-thread only — jamdict (card_for) is not worker-safe
        mined = is_mined(tok, reader._mined)
    key = panel_key(reader, tok, tok.surface, mined=mined)
    st = panel_for(reader, tok, tok.surface, min_h=reader._tip_cap(), mined=mined, nested=True)
    return st, key, tok, tok.surface, mined


def _open_engaged(reader: Reader, source: str, query: str, anchor: Anchor) -> None:
    """Open a clicked/keyed nested popup, deferring the getmask2 raster off the main thread when a worker
    is available (like the scan-hover tier-3, but anchor-CARRIED since a clicked link/kanji isn't
    scan-re-derivable). Existence-checked first, so a 'no entry' toast still fires on the click tick."""
    built = _engaged_open_panel(reader, source, query)
    if built is None:
        if source == "kanji":
            reader._toast(f"no kanji entry for {query}", "warn", 1.2)
        return
    st, key, token, word, mined = built
    request = tooltip_engaged.OpenRequest(
        source,
        query,
        (anchor.wx, anchor.wy, anchor.wh),
        id(reader._tip_state),
        mined,
    )
    if reader._request_engaged_tooltip(request):
        return
    place_nested(reader, st, key, token, word, anchor)


def open_link(reader: Reader, lb, xy, scroll: int) -> None:
    """A cross-reference link was clicked → open its target in the nested popup. A wildcard target
    (``*``/``?``) opens a search-results popup whose rows are themselves clickable links back into exact
    terms; else the whole query is looked up as one exact term."""
    q = lb.query
    sx, sy = xy
    source = "search" if any(c in q for c in "*?＊？") else "link"
    _open_engaged(reader, source, q, Anchor(sx + lb.x, sy + (lb.y - scroll), lb.h))


def open_search(reader: Reader, pattern: str, wx: float, wy: float, wh: float) -> None:
    """Open a wildcard/prefix search-results popup for ``pattern``."""
    _open_engaged(reader, "search", pattern, Anchor(wx, wy, wh))


def kanji_current(reader: Reader) -> None:
    """`k` — open the hovered word's first kanji in the nested popup; repeat cycles through
    the word's kanji."""
    if reader.dict_set is None or not (0 <= reader.hover < len(reader.tokens)):
        return
    chars = [c for c in reader.tokens[reader.hover].surface if is_ideograph(c)]
    if not chars:
        reader._toast("no kanji in this word", "warn", 1.2)
        return
    ch = chars[reader._kanji_index % len(chars)]
    ox, oy = reader.sub_origin
    b = box_for_token(reader.boxes, reader.hover)
    if b is None:
        return
    reader._kanji_index += 1
    open_kanji(reader, ch, ox + b.x, oy + b.y, b.h)


def open_kanji(reader: Reader, ch: str, wx: float, wy: float, wh: float) -> None:
    """Open the kanji entry for ``ch`` in the nested popup — deferred off the click/key tick when a
    worker is available (the getmask2 raster #294 moved off the hover path), else built synchronously."""
    _open_engaged(reader, "kanji", ch, Anchor(wx, wy, wh))


def click_kanji_fallback(reader: Reader, x: float, y: float) -> None:
    """A click on a SINGLE-ideograph scan cell whose token has no term match opens the kanji
    entry instead — reuses the nested-popup route."""
    if reader.dict_set is None:
        return
    sb = scan_hit(reader.tip, reader._raster_scale, x, y)
    if sb is None or not sb.text:
        return
    ch = sb.text[0]
    if not is_ideograph(ch):
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
        reader.interaction_surfaces.remove(OverlayId.NESTED)
    reader._nest = PopupView(OverlayId.NESTED)
