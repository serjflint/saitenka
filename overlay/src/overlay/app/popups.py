"""Popup view state: the cached tooltip panel + the per-popup view.

``Panel`` is a cached, windowed-rendered tooltip panel — a :class:`WindowedPanel` over the entry's
rows plus its reading. ``PopupView`` is the per-popup VIEW state — anchor, viewport, scroll, screen
rect, linger timer, dirty flag. The nested scan popup uses it fully; the base tooltip keeps its own
exploded ``_tip_*`` attributes (so the hover FSM and its tests stay untouched).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from overlay.app.render_cache import RenderCache
    from overlay.app.tokenize import Token
    from overlay.app.tooltip import PanelKey
    from overlay.model import Theme
    from overlay.render.banded import WindowedPanel
    from overlay.render.layout_backend import LayoutBackend


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
        # Lazy imports: overlay.body_block depends on render.document, so a module-level import of
        # render_body_band would cycle back through .render at the package level. It's injected as the
        # windowed engine's GIL-build process-pool band renderer for the same reason (see WindowedPanel).
        from overlay.body_block import render_body_band
        from overlay.render.banded import WindowedPanel

        return cls(
            WindowedPanel(
                rows,
                width,
                theme,
                compress=True,
                max_cached_blocks=band_cache_max,
                raw_band_ceiling=raw_band_ceiling,
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
                from overlay import otel_metrics

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
    """State for one popup view — today the nested scan popup (a tooltip opened by hovering a word
    *inside* another tooltip). Kept in one object so the base tooltip's own state stays untouched."""

    def __init__(self):
        self.state: Panel | None = None  # Panel of the shown word
        self.key: tuple | None = None  # its panel-cache key
        self.token: Token | None = None  # the inner Token (for mining via the popup's ⊕)
        self.word: str | None = None  # inner word surface — dedup against re-opening
        self.tail: str | None = None  # scan-cell tail that opened it — skip re-scanning
        self.xy: tuple[int, int] = (0, 0)
        self.view_h = 0
        self.scroll = 0
        self.rect: tuple[int, int, int, int] | None = None  # screen rect, for hit-testing
        self.hide_at = 0.0


class TooltipState:
    """Runtime state of the base tooltip + its hover FSM — the big, hot interaction-scoped cluster:
    the shown panel and its scroll/viewport/screen-rect, the in-place link-nav stack, the nested scan
    popup, the scan/word dwell timers, the copy-flash pulse, the hovered word's reading/terms/kanji, and
    the LRU panel cache. Grouped off the ``Reader`` god-object (#30); the ``Delegated`` shims keep every
    historical ``reader._tip_*``/``_nest``/``_scan_*``/``_hover_*``/``_flash_*``/``_panel_cache`` name, so
    the hover FSM woven through tooltip.py / nested_popup.py and its tests stay untouched."""

    def __init__(self) -> None:
        self.paused_by_tip = False  # mpv was auto-paused by a tooltip show (resume on hide)
        self.tip_rect: tuple | None = None  # (x, y, w, h) of the visible tooltip, for keep-alive
        self.hide_at = 0.0  # monotonic time to hide the tooltip (0 = not scheduled)
        self.tip_scroll = 0
        self.tip_view_h = 0
        self.tip_xy: tuple[int, int] = (0, 0)
        self.tip_state: Panel | None = None  # Panel currently shown
        self.tip_key: PanelKey | None = None  # its cache key
        # Yomitan-style in-place link nav: a clicked cross-reference pushes the prior view here; Esc pops.
        self.tip_nav: list = []
        self.nest = PopupView()  # nested scan popup (hover a word inside the tooltip → its entry)
        self.scan_target: str | None = None  # scan-cell tail the cursor is settling on (dwell)
        self.scan_since = 0.0  # when it became the target (dwell start)
        self.word_target: int | None = (
            None  # subtitle word the cursor is settling on (switch dwell)
        )
        self.word_since = 0.0
        self.last_mouse = (-1.0, -1.0)  # latest cursor pos — routes the wheel to the popup under it
        self.flash_oid: int | None = None  # a popup pulsing a "copied" highlight border
        self.flash_until = 0.0
        self.hover_reading = ""  # dict-form reading of the hovered word, for TTS
        self.hover_terms: tuple[str, ...] = ()  # multi-token terms starting at the hovered word
        self.hover_span: tuple[int, int] | None = None  # token span the longest term covers
        self.kanji_index = 0  # `k` cycles the hovered word's kanji
        self.tip_keys_bound = False
        self.tip_tok: Token | None = None  # base tooltip's source token (for the crisp re-render)
        self.tip_inflected: str | None = (
            None  # its inflected surface (re-rendered on show AND scroll)
        )
        self.crisp_miss = ""  # last blit's soft-fallback reason ("" = composited crisp) — telemetry
        self.crisp_pending = (
            False  # a soft first paint is up; poll upgrades to crisp once bands warm
        )
        self.tip_show_cold = False  # was the last base-tooltip show a panel build (vs a cache hit)
        # LRU cache (OrderedDict keyed by PanelKey), bounded at panel_cache_max; each Panel keeps only its
        # windowed blocks (compressed) so the whole cache stays small. Evict LRU on overflow, not clear.
        self.panel_cache: OrderedDict = OrderedDict()

    @property
    def open(self) -> bool:
        """Shown iff a tooltip rect is placed — the uniform ``SurfaceState`` predicate the surface
        registry (app/surfaces.py) reads for mouse-capture/routing."""
        return self.tip_rect is not None
