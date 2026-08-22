"""The nested popup: hovering (or clicking a cross-reference link inside) a word INSIDE the base
tooltip opens a depth-1 "quick look" popup for that inner word, anchored above/below it — the
Yomitan-style scan-inside-scan. Also home to kanji-lookup mode (``k``) and wildcard/prefix search
results, both of which reuse the same nested-popup anchoring.

Host-free: every entry takes `TipPorts`, `PanelPorts`, `WordLookup` or `HoverInputs`. The nested
popup's own state (``tip.nest``, a :class:`~saitenka.app.popups.PopupView`) lives on the tooltip.

Builds and blits through :mod:`saitenka.app.tooltip_panel` — the leaf importing the panel
machinery, which is the direction that does not cycle. Every one of those calls used to go back
out through a one-line ``Reader`` delegation.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from saitenka.app import tooltip_engaged
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.popups import HoverInputs, PopupView, TipPorts, WordLookup
from saitenka.app.prefetch import cap_for
from saitenka.app.subtitles import box_for_token
from saitenka.app.tooltip_panel import (
    TIP_GAP,
    PanelPorts,
    is_mined,
    panel_for,
    panel_key,
    place_panel,
    render_view,
    rows_panel,
    scan_hit,
)
from saitenka.model import is_ideograph
from saitenka.runtime import events

if TYPE_CHECKING:
    from saitenka.app.popups import TooltipState
    from saitenka.app.tooltip_panel import PanelStyle

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


def show_nested(ports: TipPorts, panel: PanelPorts, lookup: WordLookup, sb) -> None:
    """Open (or switch) the nested popup for the word starting at scan cell ``sb`` — its text is the
    Yomitan-style tail from the hovered char, so the first token is the word under the cursor. The
    popup is anchored to that inner word's on-screen cell, above/below like the base tooltip."""
    if lookup.deferred:
        from saitenka.app.hover_metadata import NestedMetadataKey, NestedMetadataRequest

        lookup.submit(
            NestedMetadataRequest(
                NestedMetadataKey(
                    lookup.prefetch_gen,
                    lookup.dependency_gen,
                    lookup.mined.generation,
                    id(ports.tip.view.state),
                    sb.text,
                ),
                lookup.tokenizer.name,
                lookup.dict_set,
                lookup.mined.snapshot(),
            )
        )
        return
    tokens = lookup.tokenizer.tokenize(sb.text)
    tok = tokens[0] if tokens else None
    if tok is None or lookup.tokenizer.is_skippable(tok):
        hide_nested(ports)
        return
    if tok.surface == ports.tip.nest.word and ports.tip.nest.state is not None:
        ports.tip.nest.tail = sb.text  # same word, new cell → don't re-scan it
        return
    # Longest-match, Yomitan-style: stack any multi-token dictionary term starting under the cursor
    # (コンサート over the over-split コン) — the same forward longest-match the base tooltip applies to a
    # hovered cue word, so an inner katakana/compound word opens whole instead of as its first morpheme.
    extra = _phrase_extra_terms(tokens, dict_set=lookup.dict_set, tokenizer=lookup.tokenizer)
    sx, sy = ports.tip.view.xy  # anchor to the inner word's screen cell
    anchor = Anchor(sx + sb.x, sy + (sb.y - ports.tip.view.scroll), sb.h)
    # defer=True: a cold inner word's head+bands raster off the main thread (tier-3), re-derived from the
    # scan cell when it lands — the hover-scan path, unlike a clicked link, is re-derivable via scan_hit.
    open_nested(
        ports,
        panel,
        tok,
        tok.surface,
        anchor,
        tail=sb.text,
        extra_terms=extra,
        defer=True,
    )


def apply_nested_metadata(ports: TipPorts, panel: PanelPorts, lookup: WordLookup, result) -> None:
    key = result.key
    if (
        result.error
        or result.token is None
        or key.generation != lookup.prefetch_gen
        or key.dependency_generation != lookup.dependency_gen
        or key.mined_generation != lookup.mined.generation
        or key.tooltip_origin != id(ports.tip.view.state)
    ):
        return
    sb = scan_hit(ports.tip, ports.scale.raster, *ports.tip.last_mouse)
    if sb is None or sb.text != key.tail:
        return
    sx, sy = ports.tip.view.xy
    anchor = Anchor(sx + sb.x, sy + (sb.y - ports.tip.view.scroll), sb.h)
    open_nested(
        ports,
        panel,
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
    ports: TipPorts,
    panel: PanelPorts,
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
        mined = is_mined(tok, panel.mined_set)
    key = panel_key(
        panel,
        tok,
        inflected,
        mined=mined,
        phrase=extra_terms,
        group_mined=group_mined,
    )
    if defer and key not in ports.tip.panel_cache:
        # Retain the identity-qualified presentation inputs while the engaged worker warms this key.
        ports.tip.nest.key = key
        ports.tip.nest.token = tok
        ports.tip.nest.word = tok.surface
        ports.tip.nest.tail = tail or tok.surface
        request = tooltip_engaged.HoverRequest(
            tok,
            inflected,
            mined,
            tuple(key),
            ports.scale.cap,
            tuple(extra_terms),
            nested=True,
            tail=tail or tok.surface,
            job_id=ports.tip.nest.job_id,
        )
        if ports.request_engaged_tooltip(request):
            return
    st = panel_for(
        panel,
        tok,
        inflected,
        min_h=ports.scale.cap,
        mined=mined,
        nested=True,
        extra_terms=extra_terms,
        group_mined=group_mined,
    )
    place_nested(ports, st, key, tok, tok.surface, anchor, tail)


def place_nested(ports: TipPorts, st, key, token, word: str, anchor: Anchor, tail=None) -> None:
    """Anchor a built :class:`Panel` ``st`` as the nested popup. ``token`` is the inner Token to mine
    via its ⊕ (None for a wildcard-search results popup, whose rows aren't a single word)."""
    ports.tip.nest.state, ports.tip.nest.key = st, key
    ports.tip.nest.token, ports.tip.nest.word = token, word
    ports.tip.nest.tail = tail
    ports.tip.nest.scroll = 0
    ports.tip.nest.desired_scroll = 0
    ports.tip.nest.view_h = nested_view_h(
        st.full_height, anchor.wy, osd_h=ports.osd[1], max_frac=ports.nested_max_frac
    )
    ports.tip.nest.xy = place_panel(
        st.width,
        anchor.wx,
        anchor.wy,
        anchor.wh,
        ports.tip.nest.view_h,
        scale=ports.scale.display,
        osd=ports.osd,
    )
    # Kick a render-ahead so a first wheel notch on the nested popup composites crisp off warm
    # bands, like the base tooltip.
    render_view(ports, ports.tip.nest)
    ports.request_render_ahead(ports.tip.nest, 1)


def rerender_with_mined_state(ports: TipPorts, panel: PanelPorts) -> None:
    """Rebuild the nested popup in place with the current mined-state, keeping its position."""
    tok = ports.tip.nest.token
    if tok is None:
        return
    mined = is_mined(tok, panel.mined_set)
    st = panel_for(panel, tok, tok.surface, min_h=ports.scale.cap, mined=mined)
    ports.tip.nest.state = st
    ports.tip.nest.key = panel_key(panel, tok, tok.surface, mined=mined)
    render_view(ports, ports.tip.nest)


def _cached_rows_panel(tip: TooltipState, style: PanelStyle, cap: int, key, entry, reading: str):
    """Fetch-or-build (and LRU-touch) the panel-cache entry for a non-token popup (kanji / search),
    measuring its head. Idempotent — the main-thread build, the worker warm, and the tick re-show all
    land on the same cached Panel."""
    st = tip.panel_cache.get_or_build(key, lambda: rows_panel(style, entry, reading))
    st.render_head(cap)
    return st


def _engaged_open_panel(
    ports: TipPorts, panel: PanelPorts, source: str, query: str, *, mined: bool | None = None
):
    """Build (or fetch cached) + measure the panel for a clicked/keyed nested open, WITHOUT placing it.
    Shared by the main-thread open (existence check + defer decision), the worker (warm the bands
    off-thread), and the tick (warm cache-hit re-show). Returns ``(panel, key, token, word, mined)`` or
    ``None`` (no entry — the caller toasts). ``source`` ∈ {``kanji``, ``search``, ``link``}. ``mined`` is
    forced by the worker (which must NOT touch jamdict via ``is_mined``); ``None`` = compute it here
    (main thread only — the tick recomputes for the freshest ⊕/✓)."""
    style = panel.style
    ds = style.dict_set
    if ds is None or style.tokenizer is None:  # the pair travels together — see `_navigated_panel`
        return None
    cap = panel.cap
    cached = partial(_cached_rows_panel, ports.tip, style, cap)
    key: tuple  # a ("kanji"/"search", …) tuple or a PanelKey (a NamedTuple) — both are tuples
    if source == "kanji":
        entry = ds.kanji_for(query, stroke_order=style.kanji_stroke_order)
        if entry is None:
            return None
        key = ("kanji", query, style.width)
        return cached(key, entry, getattr(entry, "reading", "")), key, None, query, False
    if source == "search":
        key = ("search", query, style.width)
        return cached(key, ds.search(query), ""), key, None, query, False
    # link → the WHOLE query as one exact term (never tokenize a link target); minable inner word
    tok = style.tokenizer.query_token(query)
    if tok is None:
        return None
    if mined is None:  # main-thread only — jamdict (card_for) is not worker-safe
        mined = is_mined(tok, panel.mined_set)
    key = panel_key(panel, tok, tok.surface, mined=mined)
    st = panel_for(
        panel,
        tok,
        tok.surface,
        min_h=panel.cap,
        mined=mined,
        nested=True,
    )
    return st, key, tok, tok.surface, mined


def _open_engaged(
    ports: TipPorts, panel: PanelPorts, source: str, query: str, anchor: Anchor
) -> None:
    """Open a clicked/keyed nested popup, deferring the getmask2 raster off the main thread when a worker
    is available (like the scan-hover tier-3, but anchor-CARRIED since a clicked link/kanji isn't
    scan-re-derivable). Existence-checked first, so a 'no entry' toast still fires on the click tick."""
    built = _engaged_open_panel(ports, panel, source, query)
    if built is None:
        if source == "kanji":
            ports.toast(f"no kanji entry for {query}", "warn", 1.2)
        return
    st, key, token, word, mined = built
    request = tooltip_engaged.OpenRequest(
        source,
        query,
        (anchor.wx, anchor.wy, anchor.wh),
        id(ports.tip.view.state),
        mined,
    )
    if ports.request_engaged_tooltip(request):
        return
    place_nested(ports, st, key, token, word, anchor)


def open_link(ports: TipPorts, panel: PanelPorts, lb, xy, scroll: int) -> None:
    """A cross-reference link was clicked → open its target in the nested popup. A wildcard target
    (``*``/``?``) opens a search-results popup whose rows are themselves clickable links back into exact
    terms; else the whole query is looked up as one exact term."""
    q = lb.query
    sx, sy = xy
    source = "search" if any(c in q for c in "*?＊？") else "link"
    _open_engaged(ports, panel, source, q, Anchor(sx + lb.x, sy + (lb.y - scroll), lb.h))


def open_search(
    ports: TipPorts, panel: PanelPorts, pattern: str, wx: float, wy: float, wh: float
) -> None:
    """Open a wildcard/prefix search-results popup for ``pattern``."""
    _open_engaged(ports, panel, "search", pattern, Anchor(wx, wy, wh))


def kanji_current(ports: TipPorts, panel: PanelPorts, inputs: HoverInputs) -> None:
    """`k` — open the hovered word's first kanji in the nested popup; repeat cycles through
    the word's kanji."""
    hovered = inputs.hover()
    if panel.style.dict_set is None or not (0 <= hovered < len(inputs.tokens)):
        return
    chars = [c for c in inputs.tokens[hovered].surface if is_ideograph(c)]
    if not chars:
        ports.toast("no kanji in this word", "warn", 1.2)
        return
    ch = chars[ports.word_store.current.kanji % len(chars)]
    ox, oy = inputs.sub_origin
    b = box_for_token(inputs.boxes, hovered)
    if b is None:
        return
    ports.word_store.dispatch(events.HoverKanjiAdvanced())
    open_kanji(ports, panel, ch, ox + b.x, oy + b.y, b.h)


def open_kanji(
    ports: TipPorts, panel: PanelPorts, ch: str, wx: float, wy: float, wh: float
) -> None:
    """Open the kanji entry for ``ch`` in the nested popup — deferred off the click/key tick when a
    worker is available (the getmask2 raster #294 moved off the hover path), else built synchronously."""
    _open_engaged(ports, panel, "kanji", ch, Anchor(wx, wy, wh))


def click_kanji_fallback(ports: TipPorts, panel: PanelPorts, x: float, y: float) -> None:
    """A click on a SINGLE-ideograph scan cell whose token has no term match opens the kanji
    entry instead — reuses the nested-popup route."""
    # The pair travels together, as in `_engaged_open_panel`: a link target is looked up whole and
    # the single-ideograph check tokenizes, so neither half alone can answer.
    dict_set, tokenizer = panel.style.dict_set, panel.style.tokenizer
    if dict_set is None or tokenizer is None:
        return
    sb = scan_hit(ports.tip, ports.scale.raster, x, y)
    if sb is None or not sb.text:
        return
    ch = sb.text[0]
    if not is_ideograph(ch):
        return
    toks = tokenizer.tokenize(sb.text)
    tok = toks[0] if toks else None
    if (
        tok is not None
        and len(tok.surface) == 1
        and not dict_set.has_term(tok.lemma, tok.surface, tok.reading)
    ):
        sx, sy = ports.tip.view.xy
        open_kanji(ports, panel, ch, sx + sb.x, sy + (sb.y - ports.tip.view.scroll), sb.h)


def hide_nested(ports: TipPorts) -> None:
    if ports.tip.nest.state is not None or ports.tip.nest.rect is not None:
        ports.surfaces.remove(OverlayId.NESTED)
    ports.tip.nest = PopupView(OverlayId.NESTED)
