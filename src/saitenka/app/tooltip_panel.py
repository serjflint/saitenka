"""The panel half of the tooltip: build a :class:`Panel` for a token, place it, composite its
viewport and upload it. The base tooltip and every nested/kanji/search popup render through here.

Split out of ``tooltip.py`` because the dependency between the two popup modules only runs one way
without a cycle. ``nested_popup`` is the leaf and needs the panel machinery; ``tooltip`` owns the
hover/click policy on top of it. While these lived in ``tooltip``, every nested call reached them
back through a one-line ``Reader`` delegation — a round trip through the host for an intra-feature
call, and the reason both modules read the host for things neither one owns.

Host-taking (the AGENTS.md seam pattern) for now: :func:`panel_style` is the one snapshot of the
session-lifetime build configuration, and the blit path still reaches the host for the display
scale and the surfaces.
"""

from __future__ import annotations

import dataclasses as _dc
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from saitenka import otel_metrics
from saitenka.app.lookup import card_for, entry_for
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.popups import Panel, PopupView
from saitenka.panel import Freq, panel_rows

if TYPE_CHECKING:
    from collections.abc import Collection

    from saitenka.app.controller import Reader
    from saitenka.app.popups import TooltipState
    from saitenka.render.layout_backend import LayoutBackend

TIP_GAP = 12  # px between an anchored popup and the word it points at
FLASH_BGRA = (90, 214, 255, 255)  # premultiplied BGRA of the warm highlight (RGB 255,214,90)
JLPT_DARKEN = (
    0.62  # darken the pastel underline hue for the pill name-segment so white text is legible
)
_CRISP_MIN_SCALE = (
    1.05  # below this the soft upscale IS the native render (1080p ≈ 1.0) — no crisp pass
)
log = logging.getLogger(__name__)


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
    tts_ok: bool = False
    # Per-stacked-entry mined state (aligned to cards_for order): flips a group's ⊕→✓ and, as part of
    # the key, rebuilds the panel when one stacked entry gets mined. () for single-entry words.
    group_mined: tuple[bool, ...] = ()
    # Longer multi-token phrase terms stacked above the bare word (数ある over 数); part of the key so
    # the same word hovered mid-phrase vs standalone caches distinctly. () when no longer term.
    phrase_terms: tuple[str, ...] = ()


def panel_key(
    reader: Reader,
    tok,
    inflected,
    *,
    mined: bool = False,
    phrase: tuple[str, ...] = (),
    group_mined: tuple[bool, ...] | None = None,
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
        anki_ok(reader.anki, reader._anki_capability),
        mined,
        reader._tts_ok,
        group_mined_of(tok, reader._mined, reader.dict_set, extra_terms=phrase)
        if group_mined is None
        else group_mined,
        # the stacked phrase terms are part of the base panel's identity (数 alone vs 数 under 数ある)
        phrase,
    )


def is_mined(tok, mined: Collection[str]) -> bool:
    """Is this token's word already in the deck? (its ⊕ shows ✓ instead). Cheap short-circuit
    while nothing has been mined; else a card_for lookup (lru-cached)."""
    if not mined:
        return False
    try:
        return card_for(tok).expression in mined
    except Exception:  # noqa: BLE001  # render hot path - any lookup hiccup just hides the mined mark
        return False


def group_mined_of(tok, mined, dict_set, *, extra_terms: tuple[str, ...] = ()) -> tuple[bool, ...]:
    """Per-stacked-entry mined flags (aligned to ``cards_for`` order) for a multi-reading word — each
    entry's ⊕ shows ✓ when that exact (expression, reading) is already in the deck. () when nothing is
    mined yet (cheap short-circuit) or the word has fewer than two entries (no stacking).
    ``extra_terms`` must match the panel's phrase stacking so the flags align with the shown groups."""
    if not mined or dict_set is None:
        return ()
    try:
        cards = dict_set.cards_for(tok, extra_terms=extra_terms)
    except Exception:  # noqa: BLE001  # render hot path - a lookup hiccup just hides the mined marks
        return ()
    if len(cards) < 2:
        return ()
    return tuple(c.expression in mined for c in cards)


def anki_ok(anki, capability) -> bool:
    """Is AnkiConnect reachable RIGHT NOW? Gates the ⊕ button per card show, so it appears/hides as
    the user opens/closes Anki mid-session (not frozen at startup). Kept fast: a short timeout with
    0 retries fails immediately when Anki is closed, and the result is cached ``anki_ok_ttl``
    seconds so rapid hovers don't ping repeatedly. False when mining isn't configured at all."""
    if anki is None or capability is None:
        return False
    capability.request()
    return bool(capability.value)


def _darken(rgba, f: float = JLPT_DARKEN):
    r, g, b, a = rgba
    return (round(r * f), round(g * f), round(b * f), a)


def jlpt_pill(tok, scorer) -> Freq | None:
    """A ``JLPT | Nx`` pill for the tooltip's frequency row, shown only when the word has a JLPT
    level — the same signal the subtitle draws as an underline (``Scorer._style``). The pill's hue
    is the level's underline color (darkened for legible white text), so the tooltip and the
    underline read as the same thing."""
    from saitenka.app.scoring import _is_content

    sc = scorer
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


def rareness_pill(tok, dict_set) -> Freq | None:
    """The blended-rareness "diff" pill: harmonic mean of the word's rank across every loaded freq
    dict, colored by band (:func:`fsrs.rareness_color`). Summarizes the row of 7+ per-dict pills into
    one rareness read. ``None`` when no freq dict has the word, so the caller skips it cleanly."""
    from saitenka.app.fsrs import diff_pill, harmonic_of

    ds = dict_set
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


def entry_for_tok(tok, inflected, *, dict_set, scorer, extra_terms: tuple[str, ...] = ()):
    """Look up the panel entry and fold in the blended-rareness pill and the JLPT pill (leading the
    frequency pills) when the word has them, so they mirror the subtitle underline / freq row.
    ``extra_terms`` are longer multi-token phrases starting at this word (数ある over 数); the dict set
    stacks them above the bare word.

    Never mutates the lru_cached Entry from lookup.lookup_entry / dict_set.entry_for — returns
    a shallow copy with a new freqs list so repeated calls do not accumulate pills."""
    if dict_set is None:
        entry = entry_for(tok)
    elif extra_terms:  # only the phrase path needs the expanded lookup
        entry = dict_set.entry_for(tok, inflected=inflected, extra_terms=extra_terms)
    else:
        entry = dict_set.entry_for(tok, inflected)
    extra = [p for p in (rareness_pill(tok, dict_set), jlpt_pill(tok, scorer)) if p is not None]
    if extra and hasattr(entry, "freqs"):
        # Build the pill list into a shallow copy — never mutate the cached original.
        entry = _dc.replace(entry, freqs=[*extra, *entry.freqs])
    return entry


@dataclass(frozen=True, slots=True)
class PanelStyle:
    """Everything a panel build needs that does not change between hovers.

    Deliberately NOT "the panel context": the union across the whole build chain is sixteen fields
    including a live mined set and a per-turn flag, and a value object holding those is `Reader`
    under another name. This is the session-lifetime half; per-turn facts stay parameters.
    """

    width: int
    band_cache_max: int
    raw_band_ceiling: int
    layout_backend: LayoutBackend | None
    layout_engine: str
    add_button: bool
    speak_button: bool
    dict_set: object = None
    scorer: object = None


def panel_style(reader: Reader) -> PanelStyle:
    """Snapshot the build configuration off the host. The one host read in the build chain."""
    return PanelStyle(
        width=reader.tip_width,
        band_cache_max=reader.band_cache_max,
        raw_band_ceiling=reader.raw_band_ceiling,
        layout_backend=reader.layout_backend,
        layout_engine=reader.layout_engine,
        add_button=anki_ok(reader.anki, reader._anki_capability),
        speak_button=reader._tts_ok,
        dict_set=reader.dict_set,
        scorer=reader.scorer,
    )


def _build_panel(
    style: PanelStyle,
    _key: PanelKey,
    tok,
    inflected,
    *,
    mined: bool,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
    during_scroll: bool = False,
) -> Panel:
    if otel_metrics.panel_cache_misses is not None:
        otel_metrics.panel_cache_misses.add(1)
    # kind is the base/nested IDENTITY. during_scroll flags a render triggered by the scan-hit-test
    # recomputing which cell is under a STATIONARY cursor after content moved under it (a nested popup
    # opening as a side effect of scrolling the base tooltip in the same turn), not a mouse move.
    with otel_metrics.instrumented(
        otel_metrics.render_duration_ms,
        "render",
        kind="nested" if nested else "base",
        during_scroll="1" if during_scroll else "0",
        layout_backend=style.layout_engine,
    ):
        # The base tooltip stacks the hovered word's longer phrase terms (passed in); nested popups
        # (inner scanned words) and prefetch pass none and look up the bare word only.
        entry = entry_for_tok(
            tok,
            inflected,
            dict_set=style.dict_set,
            scorer=style.scorer,
            extra_terms=extra_terms,
        )
        return Panel.from_rows(
            panel_rows(
                entry,
                style.width,
                add_button=style.add_button,
                mined=mined,
                speak_button=style.speak_button,
                group_mined=_key.group_mined,
            ),
            style.width,
            getattr(entry, "reading", "") or tok.reading,
            band_cache_max=style.band_cache_max,
            raw_band_ceiling=style.raw_band_ceiling,
            layout_backend=style.layout_backend,
        )


def panel_for(
    reader: Reader,
    tok,
    inflected=None,
    min_h: int | None = None,
    *,
    mined: bool | None = None,
    nested: bool = False,
    extra_terms: tuple[str, ...] = (),
    group_mined: tuple[bool, ...] | None = None,
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
        mined = is_mined(tok, reader._mined)
    key = panel_key(
        reader,
        tok,
        inflected,
        mined=mined,
        phrase=extra_terms,
        group_mined=group_mined,
    )
    # No `_panel_cache_get` wrapper any more: it existed to hold the fetch-or-build-then-LRU-touch
    # protocol, and `PanelCache` owns that now.
    style = panel_style(reader)
    during_scroll = reader._scrolled_this_tick
    st = reader._panel_cache.get_or_build(
        key,
        lambda: _build_panel(
            style,
            key,
            tok,
            inflected,
            mined=mined,
            nested=nested,
            extra_terms=extra_terms,
            during_scroll=during_scroll,
        ),
    )
    # The head walk+wrap (offset measure for placement) — runs on every hover, cold or warm, and was
    # the untraced bulk of tooltip_show's self-time (#158 territory). Cheap on a re-measured cached
    # panel, a full walk on a fresh one. Nests under tooltip_show / prefetch_decode.
    with otel_metrics.traced("measure"):
        st.render_head(min_h if min_h is not None else reader._tip_cap())
    return st


def place_tip(
    view, width: int, full_height: int, cap: int, anchor, *, scale: float, osd
) -> tuple[int, int]:
    """Put a panel on screen: reset the scroll, cap the height, choose the position.

    Takes the view rather than the host because every value it touches already lives on it. Width
    and height rather than a panel, because the direct-paint path (#149) places a cached ARRAY that
    has no panel yet, and it was repeating this arithmetic — including the safe area — verbatim.

    Safe area: the cap keeps the tooltip clear of the OSC header and the controls at the bottom, so
    it never spills under the window chrome. It scrolls, so capping beats trying to fit a tall entry
    — `full_height` is the windowed engine's estimate, exact once the head measured a short panel.
    """
    wx, wy, box_h = anchor
    view.scroll = 0
    view.desired_scroll = 0
    view.view_h = min(full_height, cap)
    view.xy = place_panel(width, wx, wy, box_h, view.view_h, scale=scale, osd=osd)
    return view.xy


def place_panel(
    full_w: int, wx: float, wy: float, wh: float, view_h: int, *, scale: float, osd: tuple[int, int]
) -> tuple[int, int]:
    """Choose a top-left (tx, ty) for a panel of REFERENCE size ``full_w`` × ``view_h`` anchored to an
    on-screen word box (wx, wy, wh): above it if there's room, else below, clamped to the safe area. The
    panel is composited at reference size then upscaled by ``_tip_display_scale`` at upload, so placement
    uses the DISPLAYED size. Shared by the base tooltip and nested popups."""
    s = scale
    disp_w, disp_h = full_w * s, view_h * s
    margin = max(16, round(osd[1] * 0.05))
    above_room = wy - TIP_GAP - margin
    below_room = (osd[1] - margin) - (wy + wh + TIP_GAP)
    if above_room >= disp_h or above_room >= below_room:
        ty = wy - TIP_GAP - disp_h  # above the word
    else:
        ty = wy + wh + TIP_GAP  # below the word
    tx = max(margin, min(wx, osd[0] - disp_w - margin))
    ty = max(margin, min(ty, osd[1] - margin - disp_h))
    return int(tx), int(ty)


def compose_kind(oid: int, *, navigated: bool) -> str:
    """Classify a ``tip_compose`` for telemetry so a report can separate the paints the user perceives
    as distinct: a ``nested`` scan popup, a ``clicked`` link-navigation of the base tooltip (nav stack
    non-empty), or a plain ``base`` hover. The blit already knows its ``oid``; navigation is the only
    state not in it."""
    if oid == OverlayId.NESTED:
        return "nested"
    return "clicked" if navigated else "base"


def blit_panel(
    reader: Reader,
    panel: Panel,
    scroll: int,
    view_h: int,
    xy,
    oid: int,
    *,
    soft_reason: str = "n/a",
):
    """Composite the ``[y0, y0+vh)`` viewport from the windowed engine (O(viewport)) and decorate +
    upload it. ``overscan`` renders one viewport of blocks BELOW the fold and keeps them warm, so the
    next wheel notch composites without a hot-path render. The sole popup blit — base and nested.
    ``soft_reason`` is the blitting view's crisp-miss reason (its own, so a nested soft blit doesn't
    read the base's) — attributed on the ``tip_compose`` span."""
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
        soft_reason=soft_reason or "n/a",
        scale=f"{reader._tip_display_scale:.4f}",
        kind=compose_kind(oid, navigated=bool(reader._tip_nav)),
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
    tip = reader.tip
    if tip.flash_oid == oid:  # the deadline owns when this stops being true
        b = 4  # "copied" highlight border (a brief visual pulse)
        view[:b, :] = view[-b:, :] = FLASH_BGRA
        view[:, :b] = view[:, -b:] = FLASH_BGRA
    s = reader._tip_display_scale
    if not prescaled and abs(s - 1.0) > 1e-3:  # only hi-dpi pays the resize; 1080p is a 1:1 no-op
        from saitenka.bgra import scale_bgra

        view = scale_bgra(view, s)
    tx, ty = xy
    popup = tip.nest if oid == OverlayId.NESTED else tip.view
    kind = popup.job_kind
    job_id = popup.job_id

    def settled(*, painted: bool) -> None:
        if job_id is None:
            return
        reader._interaction_jobs.finish(kind, "painted" if painted else "failed", job_id=job_id)

    # Fenced: a paint acknowledged after a newer paint or a hide settles nobody, so the intent's
    # latency is never closed out against pixels something else has already replaced.
    reader.interaction_surfaces.present_bgra(view, tx, ty, oid=oid, on_settled=settled)
    return (tx, ty, view.shape[1], view.shape[0])


def hit_target(nest, tip_state, tip_scroll: int, raster_scale: float, *, nested: bool):
    """The ``(panel, scale, scroll)`` to hit-test a popup against — the ONE reference panel, always. It's
    composited natively (glyph masks over 1× geometry), so the DRAWN panel IS the hit-tested panel and the
    inverse is a single ``(mx-sx)/scale + scroll`` against 1× geometry — the two-geometry seam bug can't
    occur. ``scale`` is the BUCKETED raster scale the blit drew at, so hit-test == draw exactly."""
    ref, scroll = (nest.state, nest.scroll) if nested else (tip_state, tip_scroll)
    return ref, raster_scale, scroll


def render_view(reader: Reader, view: PopupView) -> None:
    """The SOLE blit path (SSOT) for BOTH the base tooltip and the nested popup: composite ``view``'s
    current viewport CRISP straight from the cached native-scale panel when it's built (the common case
    once a word is shown — so scrolling stays crisp, no soft flash), else the soft reference upscale, and
    store its screen rect. Every re-blit — show, scroll, flash expiry, OSD change — routes through here,
    so nothing can flip a crisp viewport back to blurry, and each popup owns its own crisp flags."""
    st = view.state
    if st is None:
        return
    view.rect = _blit_crisp_or_soft(reader, view, st)


def apply_pending_crisp(reader: Reader, view: PopupView) -> None:
    """Poll loop: once a soft first paint's native bands are warmed by the scroll-ahead worker, re-blit
    ``view`` ONCE to upgrade soft→crisp (``_blit_native`` composites crisp when warm and clears the
    flag). Per-view, so a soft nested paint upgrades the NESTED popup (not the base). No-op until warm /
    when nothing is pending — a cheap warmth check per tick, never a re-blit-per-tick churn."""
    if not view.crisp_pending:
        return
    st = view.state
    if st is None:
        view.crisp_pending = False
        return
    vh = min(view.view_h, st.full_height)
    y0 = max(0, min(view.scroll, max(0, st.full_height - vh)))
    if st.native_viewport_warm(y0, vh, reader._raster_scale):
        render_view(
            reader, view
        )  # warm now → _blit_native composites crisp and clears crisp_pending


def _blit_native(reader: Reader, view: PopupView, st: Panel):
    """One-panel (scale-boundary) blit: composite the ONE reference panel's viewport at the display scale
    — native crisp glyph masks over the 1× geometry — and upload 1:1. Soft below the crisp threshold
    (≈1080p, where native == the upscale). No second panel, no crisp cache: the drawn panel IS the
    reference panel, so it can't disagree with the hit-test (which reads the same 1× geometry). ``view``
    owns the scroll/viewport/xy + the soft→crisp flags, so base and nested each track their own."""
    scroll, view_h, xy, oid = view.scroll, view.view_h, view.xy, view.oid
    scale = (
        reader._raster_scale
    )  # bucketed → matches hit_target's inverse; reuses cached native bands
    if scale <= _CRISP_MIN_SCALE:  # 1080p — native == soft upscale, take the cheaper 1× path
        view.crisp_miss = "not_hidpi"
        view.crisp_pending = False
        return blit_panel(reader, st, scroll, view_h, xy, oid, soft_reason=view.crisp_miss)
    full_h = st.full_height
    vh = min(view_h, full_h)
    y0 = max(0, min(scroll, max(0, full_h - vh)))
    # SOFT-FIRST (plan B3): a cold native viewport rasters O(viewport) glyph masks synchronously (~scale²
    # px) — too slow for the hot path. Paint the instant 1× upscale now, flag the poll loop to upgrade,
    # and let the scroll-ahead worker warm the native bands. Only composite crisp when they're already warm
    # (a cheap memoised assemble). This keeps show/scroll responsive; crisp lands a frame or two later.
    if not st.native_viewport_warm(y0, vh, scale):
        view.crisp_miss = "warming"
        view.crisp_pending = True  # poll's apply_pending_crisp re-blits once the bands warm
        return blit_panel(reader, st, scroll, view_h, xy, oid, soft_reason=view.crisp_miss)
    try:
        # crisp=native (soft_reason="" — this IS the crisp path, not a soft fallback). warm_only: the
        # main thread NEVER rasters — the bands are warm (gated above); a raced eviction shows bg, not a
        # synchronous raster. All rasterisation is a worker job (structural, not a thread check).
        with otel_metrics.traced(
            "tip_compose",
            soft_reason="",
            scale=f"{scale:.4f}",
            kind=compose_kind(oid, navigated=bool(reader._tip_nav)),
        ):
            arr = st.viewport(y0, vh, overscan=vh, scale=scale, warm_only=True)  # native, no raster
    except Exception:  # a composite failure falls back to the soft upscale (never a blank tooltip)
        log.debug("native compose failed", exc_info=True)
        return blit_panel(reader, st, scroll, view_h, xy, oid, soft_reason=view.crisp_miss)
    view.crisp_miss = ""
    view.crisp_pending = False
    if otel_metrics.crisp_swaps is not None:
        otel_metrics.crisp_swaps.add(1)
    # y0/full_h are display px so decorate_and_upload's scrollbar-thumb geometry stays right; the array
    # is already native (prescaled) so no scale_bgra.
    return decorate_and_upload(
        reader, arr, round(y0 * scale), round(full_h * scale), xy, oid, prescaled=True
    )


def _blit_crisp_or_soft(reader: Reader, view: PopupView, st: Panel):
    """Composite ``view``'s current viewport and return its display-px rect. One panel: the reference
    panel composites natively at the display scale (``_blit_native``), soft below the crisp threshold.
    The SSOT both popups blit through, so each is crisp exactly when hi-dpi."""
    return _blit_native(reader, view, st)


def scroll_view(reader: Reader, view: PopupView, delta: int) -> bool:
    """Scroll ``view`` by ``delta`` (clamped to the windowed full height, a converging estimate) and
    re-blit crisp, warming the NEXT native bands off the main thread so continued scrolling composites
    crisp without a synchronous raster. Shared by the base tooltip and the nested popup — nested finally
    gets the same render-ahead lookahead the base has. ``True`` iff it actually scrolled."""
    st = view.state
    if st is None:
        return False
    maxs = max(0, st.full_height - view.view_h)
    ns = min(maxs, max(0, view.desired_scroll + delta))
    if ns == view.desired_scroll:
        return False
    view.job_id = reader._interaction_jobs.begin("scroll")
    view.job_kind = "scroll"
    view.desired_scroll = ns
    view.hide_pending = False  # scrolling counts as interacting → keep this popup up
    deferred = reader._request_render_ahead(view, 1 if delta > 0 else -1)
    if not deferred:
        view.scroll = ns
        render_view(reader, view)
        return True
    if st.viewport_warm(ns, min(view.view_h, st.full_height)):
        view.scroll = ns
        render_view(reader, view)
    return True


def apply_pending_scroll(reader: Reader, view: PopupView) -> None:
    """Publish the newest desired viewport once its raw bands are fully warm."""
    st = view.state
    if st is None or view.desired_scroll == view.scroll:
        return
    view_h = min(view.view_h, st.full_height)
    if not st.viewport_warm(view.desired_scroll, view_h):
        return
    view.scroll = view.desired_scroll
    render_view(reader, view)


def scan_hit(tip: TooltipState, raster_scale: float, mx: float, my: float):
    """Which per-character scan cell of the base tooltip is under (mx, my)? Maps screen → panel
    coords (accounting for scroll) and returns the :class:`~saitenka.model.ScanBox`, or None. Hit-tests the
    panel actually DRAWN (crisp native when shown, else reference) so a hover lands on the right cell.

    Five of the six things this needed live on the tooltip state; only the raster scale does not.
    """
    if tip.view.state is None or tip.view.rect is None:
        return None
    panel, s, scroll = hit_target(  # the on-screen panel + its scale/scroll
        tip.nest, tip.view.state, tip.view.scroll, raster_scale, nested=False
    )
    if panel is None:
        return None
    sx, sy = tip.view.xy
    px = (mx - sx) / s
    py = (my - sy) / s + scroll
    return panel.windowed.scan_hit(
        int(px), int(py)
    )  # windowed hit-test (retained per-block geometry)
