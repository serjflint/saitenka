"""Popup view state: the cached tooltip panel + the per-popup view.

``Panel`` is a cached, windowed-rendered tooltip panel — a :class:`WindowedPanel` over the entry's
rows plus its reading. ``PopupView`` is the per-popup VIEW state — overlay id, anchor, viewport,
scroll, screen rect, linger timer, and its own soft→crisp flags. BOTH the base tooltip (``TooltipState.
view``) and the nested scan popup (``TooltipState.nest``) are a ``PopupView`` now, so they share one
blit/scroll/crisp path; the base's historical ``_tip_*`` names resolve onto ``tip.view.*`` through the
Reader's ``Delegated`` shims (the hover FSM and its tests stay untouched).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.interaction_jobs import InteractionJobs
from saitenka.app.overlay_ids import OverlayId

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

    from saitenka.app.hover_store import HoverStore
    from saitenka.app.interaction_surfaces import InteractionSurfaces
    from saitenka.app.prefetch import TipScale
    from saitenka.app.render_cache import RenderCache
    from saitenka.app.tokenize import Token
    from saitenka.model import Theme
    from saitenka.render.banded import WindowedPanel
    from saitenka.render.layout_backend import LayoutBackend


@dataclass(frozen=True, slots=True)
class TipPorts:
    """Everything the popup blit, scroll and placement chain reaches the host for.

    Seven members cover fifteen functions across `tooltip`, `tooltip_panel` and `nested_popup` —
    the first place in the tooltip cluster where a port is actually available, because the chain
    only ever wanted the tip's own state, the scale it draws at, and three collaborators to hand
    the pixels to. Built as `Reader.tip_ports`, so a caller still holding the host pays one member
    for it rather than the seven it gathers.

    `tip` is the live mutable `TooltipState`, not a copy: the chain writes scroll and crisp flags
    back onto the view it was given, and a snapshot would silently drop those.
    """

    tip: TooltipState
    scale: TipScale
    surfaces: InteractionSurfaces
    hover_store: HoverStore
    request_render_ahead: Callable[[PopupView, int], bool]
    osd: tuple[int, int]
    nested_max_frac: float


class Panel:
    """A cached tooltip panel: a windowed (banded) renderer over the entry's rows plus its reading.

    The windowed engine composites each viewport O(viewport) from a per-block pixel cache (retained
    zlib-compressed, so a cached/warmed panel keeps the old blob path's memory profile), estimates the
    full height as blocks measure, and owns hit-testing. One path for the base tooltip and every
    nested / kanji / search popup — no whole-panel blob, no deferred-tail finish."""

    def __init__(self, windowed: WindowedPanel, reading: str):
        self.windowed = windowed
        self.reading = reading

    @classmethod
    def from_rows(
        cls,
        rows,
        width: int,
        reading: str,
        *,
        theme: Theme | None = None,
        band_cache_max: int | None = None,
        raw_band_ceiling: int = 0,
        layout_backend: LayoutBackend | None = None,
    ) -> Panel:
        """Wrap ``rows`` in the windowed engine (the sole tooltip compositor). Bands stay raw for a fast
        first scroll-reach until the panel's estimate crosses ``raw_band_ceiling`` bytes, when they zlib
        so a giant entry can't blow the retained budget (``0`` = always compress). ``band_cache_max``
        caps retained render bands per panel (``None`` = keep exactly the viewport±overscan).
        ``layout_backend`` picks the block-geometry engine (``None`` = the default). ``theme`` MUST match
        the one the rows were built at — the windowed engine takes the top/bottom margin and inter-row
        gaps from it, so a scaled native panel (``Theme(scale)``) needs it forwarded or its vertical
        geometry silently falls back to scale-1.0 while the row content is scaled. Shared by the base
        tooltip and the nested/kanji/search popups."""
        # Lazy imports: panel.body depends on render.document, so a module-level import of
        # render_body_band would cycle back through .render at the package level. It's injected as the
        # windowed engine's GIL-build process-pool band renderer for the same reason (see WindowedPanel).
        from saitenka.panel.body import render_body_band
        from saitenka.render.banded import BandedTuning, WindowedPanel

        return cls(
            WindowedPanel(
                rows,
                width,
                theme,
                tuning=BandedTuning(
                    compress=True,
                    max_cached_blocks=band_cache_max,
                    raw_band_ceiling=raw_band_ceiling,
                ),
                render_block_fn=render_body_band,
                layout_backend=layout_backend,
            ),
            reading,
        )

    @property
    def width(self) -> int:
        return self.windowed.width

    @property
    def full_height(self) -> int:
        """Best current estimate of the whole-panel height — exact for measured blocks, converging as
        the rest render. Drives placement, the scroll clamp, and the scrollbar."""
        return self.windowed.full_height

    @property
    def retained_nbytes(self) -> int:
        """On-heap footprint of the retained (compressed) blocks — the panel-cache gauge."""
        return self.windowed.retained_nbytes

    @property
    def last_frame_rasters(self) -> int:
        """Bands rasterised synchronously by the last :meth:`viewport` — the jank driver a slow
        scroll_frame/tooltip_show span records (0 = a warm frame; N = render_ahead was behind)."""
        return self.windowed.last_frame_rasters

    def render_head(self, min_h: int) -> None:
        """Warm + measure the head prefix so placement has a real height and a later hover is a cache
        hit (also the speculative-prefetch entry point). Cheap: renders only the head blocks."""
        self.windowed.measure_to(min_h)

    def precompose_head(
        self,
        cap: int,
        *,
        cache: RenderCache | None = None,
        config_sig: str | None = None,
        content_key: str | None = None,
        min_height: int = 0,
        protected: bool = False,
    ) -> None:
        """Composite the FIRST viewport (scroll=0) in idle so a warm hover is copy + decorate + upload,
        not a synchronous re-composite. ``cap`` is the show's viewport-height cap; the composited height
        matches the show's ``view_h = min(full_height, cap)`` and its ``overscan = view_h`` look-ahead.
        Call after :meth:`render_head`/a full build has measured the head (so ``full_height`` is set).

        With a persistent ``cache`` + keys, a cost-gated head (``full_height >= min_height`` — the
        pathological tail #149 targets) is also written to disk here, so a *later session*'s cold hover
        seeds it via :meth:`load_precomposed_head` and skips the raster. ``protected`` marks the offline
        prewarm's popular set as eviction-last (see :meth:`RenderCache.put`)."""
        view_h = min(self.full_height, cap)
        if view_h <= 0:
            return
        self.windowed.precompose(view_h, overscan=view_h)
        if cache is not None and config_sig is not None and content_key is not None:
            self._store_precomposed(cache, config_sig, content_key, min_height, protected=protected)

    def _store_precomposed(
        self,
        cache: RenderCache,
        config_sig: str,
        content_key: str,
        min_height: int,
        *,
        protected: bool,
    ) -> None:
        """Persist the just-composited first viewport iff it clears the cost gate (``full_height >=
        min_height``). Off the main thread (the prefetch worker / offline builder call this)."""
        if self.full_height < min_height:
            return
        fv = self.windowed.first_view
        if fv is not None:
            cache.put(
                config_sig, content_key, fv[0], fv[1], self.full_height, fv[2], protected=protected
            )
            if (
                not protected
            ):  # a LIVE write-back (prewarm's protected fills aren't session telemetry)
                from saitenka import otel_metrics

                if otel_metrics.render_cache_writebacks is not None:
                    otel_metrics.render_cache_writebacks.add(1)

    def load_precomposed_head(
        self, cap: int, cache: RenderCache, config_sig: str, content_key: str
    ) -> bool:
        """Seed the first viewport from the persistent cache so a cold hover is copy+upload with no head
        raster. ``True`` on a hit. Call after the head is measured (``full_height`` set) so the requested
        ``view_h`` matches the show's; a differing height / config simply misses (safe → live render)."""
        view_h = min(self.full_height, cap)
        if view_h <= 0:
            return False
        loaded = cache.get(config_sig, content_key, view_h, view_h)
        if loaded is None:
            return False
        self.windowed.install_first_view(loaded.view_h, loaded.overscan, loaded.array)
        return True

    def viewport(
        self,
        scroll: int,
        view_h: int,
        overscan: int = 0,
        *,
        scale: float = 1.0,
        warm_only: bool = False,
    ) -> np.ndarray:
        """Composite the ``[scroll, scroll+view_h)`` viewport as a premultiplied BGRA array via the
        per-band BGRA fast path (#138). ``overscan`` warms one screen of blocks below the fold. ``scale``
        > 1 composites the crisp NATIVE viewport over the same 1× geometry (scale-boundary arch).
        ``warm_only`` (the main-thread path) composites cached bands only — a miss is background, never a
        synchronous raster; a worker warms it."""
        return self.windowed.viewport_bgra(
            scroll, view_h, overscan=overscan, scale=scale, warm_only=warm_only
        )

    def native_viewport_warm(self, scroll: int, view_h: int, scale: float) -> bool:
        """True when the native ``[scroll, scroll+view_h)`` viewport is already cached at ``scale`` — a
        cheap crisp compose. The blit paints soft on a cold viewport and upgrades once this goes true."""
        return self.windowed.native_viewport_warm(scroll, view_h, scale)

    def viewport_warm(self, scroll: int, view_h: int) -> bool:
        return self.windowed.viewport_warm(scroll, view_h)

    def render_ahead(
        self, scroll: int, view_h: int, *, direction: int, should_cancel, scale: float = 1.0
    ) -> int:
        """Warm the blocks just beyond the viewport in the scroll ``direction`` — the off-main-thread
        counterpart to :meth:`viewport`'s synchronous ``overscan``. ``overscan=view_h`` starts the
        warm past the screen the blit already rendered, so a fast scroll finds them cached. ``scale`` > 1
        warms NATIVE bands (one-panel crisp path)."""
        return self.windowed.render_ahead(
            scroll,
            view_h,
            direction=direction,
            overscan=view_h,
            should_cancel=should_cancel,
            scale=scale,
        )


class PopupView:
    """View state for ONE popup — the base tooltip *or* the nested scan popup. Holds everything the
    shared blit/scroll/crisp machinery needs (``state`` panel, viewport, scroll, screen rect, and its
    own soft→crisp upgrade flags), keyed by ``oid`` so each popup composites to its own saitenka. Kept in
    one object so the base tooltip and the nested popup run the same code path (crisp-poll, render-ahead)
    while their state stays independent — a nested soft paint no longer flips the base's crisp flag."""

    def __init__(self, oid: int = OverlayId.TIP):
        self.oid = oid  # which overlay this view composites to (TIP / NESTED)
        self.state: Panel | None = None  # Panel of the shown word
        self.key: tuple | None = None  # its panel-cache key
        self.token: Token | None = None  # the inner Token (for mining via the popup's ⊕)
        self.word: str | None = None  # inner word surface — dedup against re-opening
        self.tail: str | None = None  # scan-cell tail that opened it — skip re-scanning
        self.xy: tuple[int, int] = (0, 0)
        self.view_h = 0
        self.scroll = 0
        self.desired_scroll = 0
        self.job_id: int | None = None
        self.job_kind = "tooltip"
        self.rect: tuple[int, int, int, int] | None = None  # screen rect, for hit-testing
        self.hide_pending = False  # a linger deadline is armed to hide this popup
        self.crisp_miss = ""  # last blit's soft-fallback reason ("" = composited crisp) — telemetry
        self.crisp_pending = (
            False  # a soft first paint is up; poll upgrades to crisp once bands warm
        )


class PanelCache:
    """Bounded LRU of rendered panels, with the lock next to the data it guards.

    It used to be three separate things on the Reader — the `OrderedDict`, a `panel_cache_max`, and
    a lock shared with an unrelated cache — and the fetch-or-build-then-LRU-touch dance around them
    was written out twice, in `tooltip` and in `nested_popup`. Two copies of a lock protocol is one
    copy too many: the second is where the `move_to_end` outside the lock lives.
    """

    def __init__(self, limit: int, lock) -> None:
        self._entries: OrderedDict = OrderedDict()
        self._limit = limit
        self._lock = lock

    def __contains__(self, key) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key):
        """The entry without an LRU touch — for a caller only asking whether it is warm."""
        return self._entries.get(key)

    def discard(self, key) -> None:
        """Drop one entry if present. Deliberate eviction (a benchmark forcing a cold paint), so no
        eviction metric fires — that counter measures pressure, not a caller asking."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop everything. A cold restart of the cache, not an eviction — no metric fires."""
        with self._lock:
            self._entries.clear()

    def values(self):
        return self._entries.values()

    def get_or_build(self, key, build):
        """Return the cached panel, building it OUTSIDE the lock if absent.

        First-writer-wins on a race: two workers building the same panel produce equivalent results,
        so the loser's is discarded rather than replacing a panel another view may already hold.
        """
        from saitenka import otel_metrics

        cached = self._entries.get(key)
        if cached is not None:
            if otel_metrics.panel_cache_hits is not None:
                otel_metrics.panel_cache_hits.add(1)
            self.touch(key)
            return cached
        built = build()
        with self._lock:
            return self._setdefault(key, built)

    def touch(self, key) -> None:
        with self._lock:
            try:
                self._entries.move_to_end(key)
            except KeyError:
                pass  # evicted between the read and the touch — harmless

    def setdefault(self, key, panel):
        with self._lock:
            return self._setdefault(key, panel)

    def _setdefault(self, key, panel):
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key]
        from saitenka import otel_metrics

        while len(self._entries) >= self._limit:
            self._entries.popitem(last=False)  # FIFO/LRU: oldest out
            if otel_metrics.panel_cache_evictions is not None:
                otel_metrics.panel_cache_evictions.add(1)
        self._entries[key] = panel
        return panel


@dataclass(frozen=True, slots=True)
class HoverMetadata:
    """What a hover lookup resolved: the phrase it found and whether it is already mined."""

    terms: tuple[str, ...] = ()
    span: tuple[int, int] | None = None
    mined: bool = False
    group_mined: tuple[bool, ...] = ()


#: No word is hovered, or its lookup was retired. Named so the clear path is one assignment rather
#: than four literals a reader has to recognise as "empty".
NO_HOVER_METADATA = HoverMetadata()


class TooltipState:
    """Runtime state of the base tooltip + its hover FSM — the big, hot interaction-scoped cluster:
    the shown panel and its scroll/viewport/screen-rect, the in-place link-nav stack, the nested scan
    popup, the scan/word dwell timers, the copy-flash pulse, the hovered word's reading/terms/kanji, and
    the LRU panel cache. Grouped off the ``Reader`` god-object (#30); the ``Delegated`` shims keep every
    historical ``reader._tip_*``/``_nest``/``_scan_*``/``_hover_*``/``_flash_*``/``_panel_cache`` name, so
    the hover FSM woven through tooltip.py / nested_popup.py and its tests stay untouched."""

    def __init__(self, *, panel_cache_max: int = 64, cache_lock=None) -> None:
        """`cache_lock` is shared with the Reader's other cache accounting, so it is injected."""
        self.paused_by_tip = False  # mpv was auto-paused by a tooltip show (resume on hide)
        self.hide_pending = False  # a linger deadline is armed to hide the tooltip
        # The base tooltip's own view state (panel/scroll/viewport/rect/crisp flags), sharing the same
        # PopupView type + blit machinery as the nested popup. The historical flat names (_tip_state,
        # _tip_scroll, …) keep resolving here through the Reader's Delegated("tip.view", …) shims.
        self.view = PopupView(OverlayId.TIP)
        # Yomitan-style in-place link nav: a clicked cross-reference pushes the prior view here; Esc pops.
        self.tip_nav: list = []
        self.nest = PopupView(
            OverlayId.NESTED
        )  # nested scan popup (hover a word inside the tooltip)
        self.scan_target: str | None = None  # scan-cell tail the cursor is settling on (dwell)
        self.word_target: int | None = (
            None  # subtitle word the cursor is settling on (switch dwell)
        )
        self.last_mouse = (-1.0, -1.0)  # latest cursor pos — routes the wheel to the popup under it
        self.flash_oid: int | None = None  # a popup pulsing a "copied" highlight border
        self.hover_reading = ""  # dict-form reading of the hovered word, for TTS
        #: What a metadata lookup resolved about the hovered word. Replaced wholesale, never field
        #: by field: the four values are one lookup's answer, and four separate assignments is how a
        #: half-updated hover (new terms, stale mined flags) reaches a draw.
        self.hover = NO_HOVER_METADATA
        self.kanji_index = 0  # `k` cycles the hovered word's kanji
        self.tip_keys_bound = False
        self.tip_tok: Token | None = None  # base tooltip's source token (for the crisp re-render)
        self.tip_inflected: str | None = (
            None  # its inflected surface (re-rendered on show AND scroll)
        )
        self.tip_show_cold = False  # was the last base-tooltip show a panel build (vs a cache hit)
        # LRU cache (OrderedDict keyed by PanelKey), bounded at panel_cache_max; each Panel keeps only its
        # windowed blocks (compressed) so the whole cache stays small. Evict LRU on overflow, not clear.
        self.panel_cache = PanelCache(panel_cache_max, cache_lock or threading.Lock())
        # The tooltip's own bounded background work — the panel build and the scroll-ahead raster.
        # Both lanes are speculative work for the panel this state describes, which is why a hover
        # that supersedes cancels them together; on the Reader it read as session infrastructure.
        self.jobs = InteractionJobs()

    @property
    def open(self) -> bool:
        """Shown iff a tooltip rect is placed — the uniform ``SurfaceState`` predicate the surface
        registry (app/surfaces.py) reads for mouse-capture/routing."""
        return self.view.rect is not None
