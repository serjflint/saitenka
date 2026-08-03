"""Windowed viewport compositor — assemble only the visible blocks, driven by the pure geometry core.

The PIL half of the banded engine (:mod:`overlay.render.window` stays PIL-free so it can be
mutation-audited). :func:`composite_window` copies each *visible* block into a fresh
background-filled viewport buffer at its ``start - scroll`` offset, clipping the top/bottom block to
the viewport edge. Because whole blocks never overlap in y (a gap sits between them) and the offsets
are integers, each disjoint copy is seam-exact — the result is byte-for-byte identical to cropping a
one-shot :func:`overlay.panel.compose_panel` stack of the same blocks (proven in
``tests/test_banded_composite.py``). This bounds assembly work to O(viewport), never O(panel)."""

from __future__ import annotations

import threading
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw

from overlay import otel_metrics
from overlay.parallel import shared_executor
from overlay.render.window import OffsetTable

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Future
    from typing import Any

    from overlay.body_block import BodyRenderArgs
    from overlay.model import RGBA, LinkBox, ScanBox, Theme
    from overlay.panel import Row
    from overlay.render.layout_backend import LayoutBackend

# The def-body renderer is injected by the caller (app/popups.py, which builds WindowedPanel) rather
# than imported here: body_block.py depends on render.document, so a module-level import would cycle
# back through .render at the package level. render_block_fn now only GATES the GIL-build process pool
# (None → hermetic in-process warm); the actual band renders use Row.render_window (threads) or a
# function-scope import of render_body_band (process pool). See WindowedPanel's render_block_fn param.


# A row taller than one viewport is cached and rasterised in viewport-sized BANDS, not as one whole
# block: a first-reach or a warm scroll frame then costs O(band) getmask2, never O(block). 256px is the
# tuned unit — a cold band rasters in ~9ms at real width (measured ≈0.034ms/px), under the 16ms smooth
# budget even on a miss, and is warmable ~18× inside render_ahead's ~0.16s flick lead (see Stage 8 /
# vibe/windowed-raster-pr3-plan.md). Small rows (header/chip/freq) are one band — never split.
_BAND_PX = 256


def _row_bands(height: int, band_px: int = _BAND_PX) -> list[tuple[int, int, int]]:
    """Tile a row of ``height`` px into ``(band_index, y0, y1)`` bands of ≤ ``band_px`` — the render +
    cache unit. A ≤ ``band_px`` row is one band ``(0, 0, height)``; every band but the last is exactly
    ``band_px`` tall so bands tile the row seam-exactly (a band == the full row cropped to its slice)."""
    if height <= 0:
        return [(0, 0, 0)]
    return [(b, y0, min(height, y0 + band_px)) for b, y0 in enumerate(range(0, height, band_px))]


@dataclass(frozen=True, slots=True)
class CachedBlock:
    """One rendered BAND held in the pixel cache: its x-offset, its ``y`` top *within the row*, its
    pixel height, row-local scan/link hitboxes, and the image kept either live or (to shrink retained
    memory, as the whole-panel path does today) zlib-compressed. Row heights are NOT stored here — they
    live in the offset table and are retained past pixel eviction, so offsets stay exact."""

    x: int
    y: int  # band top within its row (row-local); absolute top = offsets.start(row) + y
    h: int  # band pixel height (== y1 - y0)
    scan: list[ScanBox]  # row-local (band-space boxes shifted by +y)
    links: list[LinkBox]
    _img: Image.Image | None = None
    _packed: tuple[bytes, tuple[int, int]] | None = None  # (zlib(rgba bytes), (w, h))

    @classmethod
    def make(cls, x: int, y: int, img: Image.Image, scan, links, *, compress: bool) -> CachedBlock:
        if compress:
            rgba = img.convert("RGBA")
            packed = (zlib.compress(rgba.tobytes(), 1), rgba.size)
            return cls(x, y, img.height, scan, links, _packed=packed)
        return cls(x, y, img.height, scan, links, _img=img)

    def image(self) -> Image.Image:
        if self._img is not None:
            return self._img
        assert self._packed is not None
        data, size = self._packed
        return Image.frombytes("RGBA", size, zlib.decompress(data))

    @property
    def nbytes(self) -> int:
        """Retained on-heap pixel size: the compressed blob length when packed, else the raw RGBA
        bytes. Summed by :meth:`WindowedPanel.retained_nbytes` for the panel-cache gauge."""
        if self._packed is not None:
            return len(self._packed[0])
        assert self._img is not None
        w, h = self._img.size
        return w * h * 4


@dataclass(frozen=True, slots=True)
class BlockGeom:
    """A block's retained hit geometry: its x-offset and row-local scan/link boxes. Kept even after the
    block's pixels are evicted, so a hover over a scrolled-away block still resolves."""

    x: int
    scan: list[ScanBox]
    links: list[LinkBox]


@dataclass(frozen=True, slots=True)
class RenderedBlock:
    """A freshly rendered BAND before it enters the cache — the value a worker thread returns. ``y0`` is
    the band's row-local top; ``scan``/``links`` come back band-space and are shifted to row-local on
    store."""

    index: int  # row
    band: int
    x: int
    y0: int  # band top within the row (row-local)
    image: Image.Image
    scan: list[ScanBox]  # band-space (shifted +y0 into row-local on store)
    links: list[LinkBox]


def composite_window(
    blocks: Sequence[tuple[int, Image.Image] | None],
    table: OffsetTable,
    scroll: int,
    viewport: int,
    *,
    width: int,
    background: RGBA,
    overscan: int = 0,
) -> Image.Image:
    """Assemble the ``[scroll, scroll+viewport)`` viewport from the visible subset of ``blocks``.

    ``blocks[i]`` is ``(x, image)`` for block ``i``, index-aligned to ``table`` (whose ``starts[i]``
    is that block's content-space top), or ``None`` for a not-yet-rendered block outside the viewport.
    Only blocks in ``table.visible_range(scroll, viewport, overscan)`` are touched; each is clipped to
    the viewport by an integer crop and copied at its offset. Any ``overscan`` beyond the exact visible
    set is harmless — those blocks clip to zero height (or are ``None``) and are skipped."""
    out = Image.new("RGBA", (width, max(viewport, 1)), background)
    start, end = table.visible_range(scroll, viewport, overscan)
    for i in range(start, end):
        block = blocks[i]
        if block is None:
            continue
        x, im = block
        top = (
            table.starts[i] - scroll
        )  # block top in viewport space (may be < 0 or past the bottom)
        src_y0 = max(0, -top)  # first block row that lands inside the viewport
        dst_y = max(0, top)
        h = min(im.height - src_y0, viewport - dst_y)  # rows of this block visible in the viewport
        if h <= 0:
            continue
        crop = im if src_y0 == 0 and h == im.height else im.crop((0, src_y0, im.width, src_y0 + h))
        out.alpha_composite(crop, (x, dst_y))
    return out


class WindowedPanel:
    """Drive :func:`overlay.panel.panel_rows` from the geometry core: MEASURE a row's height when the
    visible range reaches it, rasterise + retain it in viewport-sized BANDS, and composite viewports
    with :func:`composite_window`.

    Each row is one block; its trailing gap is known at build time (``Row.gap``). Building the panel
    walks no content (the deferred-thunk contract). Reaching scroll ``S`` MEASURES every row above ``S``
    exactly once — body rows lay out only (``Row.measure`` — no ``getmask2``), so incremental offsets
    stay exact without rastering a tall block — then renders only the BANDS overlapping the viewport,
    evicting bands outside viewport±overscan. This bounds both per-frame raster AND retained pixels to
    O(viewport) even when one row is 34× the viewport (the PR3 crux). Byte-for-byte identical to cropping
    a one-shot :func:`overlay.panel.render_panel` at the same offset (proven in
    ``tests/test_windowed_panel.py``; a band is a shorter block at a within-row offset)."""

    def __init__(
        self,
        rows: Sequence[Row],
        width: int,
        theme: Theme | None = None,
        top_reserve: int = 0,
        *,
        seed_height: int = 200,
        max_cached_blocks: int | None = None,
        compress: bool = False,
        render_block_fn: Callable[[BodyRenderArgs, int, int], tuple] | None = None,
        layout_backend: LayoutBackend | None = None,
    ):
        from overlay.model import _DEFAULT_THEME
        from overlay.render.layout_backend import DEFAULT_BACKEND
        from overlay.render.window import LazyOffsets

        self.theme = theme if theme is not None else _DEFAULT_THEME
        self.width = width
        self.top_reserve = top_reserve
        self._rows = list(rows)
        self._cap = max_cached_blocks  # LRU pixel-cache cap (None = keep exactly visible±overscan)
        self._compress = compress
        # A truthy value ENABLES the GIL-build process pool in render_ahead (the actual band renderer,
        # render_body_band, is a function-scope import — module-level would cycle body_block →
        # render.document → back through .render). None keeps render_ahead in-process (hermetic tests).
        self._render_block_fn = render_block_fn
        gaps = [r.gap if r.gap is not None else self.theme.gap for r in self._rows]
        self._offsets = LazyOffsets(
            gaps,
            self.theme.margin + top_reserve,
            self.theme.margin,
            seed_height=seed_height,
            backend=layout_backend if layout_backend is not None else DEFAULT_BACKEND,
        )
        # LRU pixel cache (oldest first), keyed by (row, band): a row is rasterised + retained in
        # viewport-sized bands so even one pathologically tall block holds only O(viewport) pixels.
        # Heights live in self._offsets and are never dropped, so offsets stay exact after eviction.
        self._blocks: OrderedDict[tuple[int, int], CachedBlock] = OrderedDict()
        # Per-band opaque premul-BGRA, converted once (#138) so a warm scroll frame is disjoint numpy
        # row-copies, not a per-frame whole-viewport convert. Keyed like _blocks; dropped on re-store/evict.
        self._bgra: dict[tuple[int, int], np.ndarray] = {}
        self._bg_bgra: np.ndarray | None = None  # theme.bg as a (4,) premul-BGRA pixel; memoised
        # Retained per-ROW hit geometry, accumulated across the row's bands (row-local boxes), NEVER
        # evicted — so a hover resolves even when a band's pixels are gone. _geom_seen dedups a line
        # drawn in two adjacent bands at their shared seam.
        self._geom: dict[int, BlockGeom] = {}
        self._geom_seen: dict[int, set] = {}
        # Guards the shared cache/geometry/offsets so a prefetch worker and the main thread can touch
        # them concurrently (mirrors the controller's _cache_lock). Reentrant: a locked public method
        # calls the lock-free internal helpers freely. FreeType faces are already thread-local, but the
        # lock is what keeps _blocks/_geom/_offsets race-free under free-threading.
        self._lock = threading.RLock()

    @property
    def count(self) -> int:
        return len(self._rows)

    @property
    def measured(self) -> int:
        """Blocks whose height is known (the exact-offset prefix)."""
        return self._offsets.prefix_len

    @property
    def full_height(self) -> int:
        """Best current estimate of the whole panel's pixel height (incl. margins) — exact for measured
        blocks, seeded for the rest, converging to exact as blocks render. Drives the scroll clamp and
        scrollbar without the eager whole-panel blob render. Matches a one-shot ``render_panel`` height
        once every block is measured (same ``total`` as :class:`OffsetTable`)."""
        with self._lock:
            return self._offsets.total_estimate()

    @property
    def cached_blocks(self) -> int:
        """Blocks whose pixels are currently retained (bounded by ``max_cached_blocks`` if set, else by
        the viewport±overscan window)."""
        return len(self._blocks)

    @property
    def retained_nbytes(self) -> int:
        """On-heap pixel footprint of the retained blocks (compressed when ``compress=True``). The
        panel-cache gauge sums this across cached panels — the windowed successor to the old blob's
        ``packed_nbytes``."""
        with self._lock:
            return sum(b.nbytes for b in self._blocks.values())

    def measure_to(self, px: int) -> None:
        """Render + cache blocks top-down until the exact-offset prefix covers ``px`` px — warms the
        head and tightens :attr:`full_height` without compositing a viewport image. Used to warm a
        panel's head for placement (a real hover) and for speculative prefetch."""
        with self._lock:
            self._grow_prefix(px)

    def _add_geom(self, i: int, x: int, scan: list, links: list) -> None:
        """Accumulate a band's ROW-LOCAL hit boxes into row ``i``'s retained geometry (never evicted, so
        a hover resolves over an evicted band). Dedups: a line straddling a band seam is drawn in both
        bands and would emit the same box twice — the seen-set keeps geometry (and box counts) clean."""
        g = self._geom.get(i)
        if g is None:
            g = BlockGeom(x, [], [])
            self._geom[i] = g
            self._geom_seen[i] = set()
        seen = self._geom_seen[i]
        for sb in scan:
            key = ("s", sb.text, sb.x, sb.y, sb.w, sb.h)
            if key not in seen:
                seen.add(key)
                g.scan.append(sb)
        for lb in links:
            key = ("l", lb.query, lb.x, lb.y, lb.w, lb.h)
            if key not in seen:
                seen.add(key)
                g.links.append(lb)

    def _store(self, rb: RenderedBlock) -> CachedBlock:
        """Cache a rendered BAND's pixels + row-local geometry. Call under ``self._lock``. Row heights
        are set at MEASURE time (:meth:`_ensure_measured`), not here — a band doesn't know the row's full
        height. Idempotent: re-storing overwrites/re-touches the LRU entry and is deduped in geometry."""
        from overlay.model import LinkBox, ScanBox

        scan_rl = [ScanBox(s.text, s.x, s.y + rb.y0, s.w, s.h) for s in rb.scan]  # band → row-local
        links_rl = [LinkBox(k.query, k.x, k.y + rb.y0, k.w, k.h) for k in rb.links]
        self._add_geom(rb.index, rb.x, scan_rl, links_rl)
        block = CachedBlock.make(rb.x, rb.y0, rb.image, scan_rl, links_rl, compress=self._compress)
        key = (rb.index, rb.band)
        self._blocks[key] = block
        self._blocks.move_to_end(key)  # LRU touch
        self._bgra.pop(key, None)  # a re-store replaces the pixels → the BGRA memo is stale
        return block

    def _render_band(self, i: int, b: int, y0: int, y1: int) -> RenderedBlock:
        """Rasterise row ``i``'s band ``[y0, y1)`` WITHOUT touching shared state — safe on a worker
        thread (fonts are thread-local; the row's memoised layout handle is walk-once). The caller
        stores the result under the lock. Body rows go through ``render_window`` (O(band) getmask2)."""
        row = self._rows[i]
        assert row.render_window is not None  # only body rows are banded
        img, scan, links = row.render_window(y0, y1)
        return RenderedBlock(i, b, row.x, y0, img, scan, links)

    def _miss(self, px: int) -> None:
        if otel_metrics.block_cache_misses is not None:
            otel_metrics.block_cache_misses.add(1)
        if otel_metrics.block_rendered_px is not None:
            otel_metrics.block_rendered_px.record(px)  # per BAND now, not per whole block

    def _hit(self) -> None:
        if otel_metrics.block_cache_hits is not None:
            otel_metrics.block_cache_hits.add(1)

    def _ensure_measured(self, i: int, *, cache_nonbody: bool = True) -> int:
        """Learn row ``i``'s full height into the offset table WITHOUT rastering a tall body — body rows
        call the layout-only ``measure()`` (no ``getmask2``); non-body rows (header/chip/freq, small —
        one band) render once. ``cache_nonbody=False`` (measure-ahead off the main thread) measures a
        non-body row's height but drops its pixels, so warming stays a band-granular decision."""
        if self._offsets.known(i):
            return self._offsets.height(i)
        row = self._rows[i]
        if row.measure is not None:  # body row — layout only, no raster
            h = row.measure()
            self._offsets.set_height(i, h)
            if row.geometry is not None:  # retain hitboxes now, so a measured row is hoverable
                scan, links = row.geometry()
                self._add_geom(i, row.x, scan, links)
            return h
        img, scan, links = row.render()  # non-body — cheap; render learns its height
        self._offsets.set_height(i, img.height)
        if cache_nonbody:
            self._store(RenderedBlock(i, 0, row.x, 0, img, scan, links))
            self._miss(img.height)
        return img.height

    def _ensure_bands(self, i: int, lo: int, hi: int) -> None:
        """Render + cache row ``i``'s bands overlapping the ROW-LOCAL window ``[lo, hi)`` (a re-render of
        an evicted band scrolled back in). The row must be measured. Non-body rows are one band."""
        row = self._rows[i]
        h = self._offsets.height(i)
        if row.render_window is None:  # non-body single band
            if (i, 0) in self._blocks:
                self._blocks.move_to_end((i, 0))
                self._hit()
            else:
                img, scan, links = row.render()
                self._store(RenderedBlock(i, 0, row.x, 0, img, scan, links))
                self._miss(img.height)
            return
        for b, y0, y1 in _row_bands(h):
            if y1 <= lo or y0 >= hi:  # band outside the requested window
                continue
            if (i, b) in self._blocks:
                self._blocks.move_to_end((i, b))
                self._hit()
            else:
                self._store(self._render_band(i, b, y0, y1))
                self._miss(y1 - y0)

    def _grow_prefix(self, target_y: int, *, cache_nonbody: bool = True) -> None:
        """Measure rows forward (top-down) until the exact-offset prefix covers ``target_y`` px — the
        cheap walk/measure, NOT a raster (body rows only lay out). ``cache_nonbody=False`` for the
        off-thread measure-ahead: learn heights past the viewport without caching non-body pixels."""
        i = self._offsets.prefix_len
        while i < len(self._rows) and self._offsets.start(i) < target_y:
            self._ensure_measured(i, cache_nonbody=cache_nonbody)
            i += 1

    def _band_top(self, key: tuple[int, int], cb: CachedBlock) -> int | None:
        """Absolute (content-space) top of a cached band, or ``None`` if its row isn't measured."""
        i = key[0]
        if not self._offsets.known(i):
            return None
        return self._offsets.start(i) + cb.y

    def _evict(self, lo: int, hi: int) -> None:
        """Bound retained pixels to O(viewport) — per BAND, so even one pathologically tall row keeps
        only the bands overlapping ``[lo, hi)`` (the viewport±overscan), never the whole block. With no
        cap, drop every band outside the window; with a cap, keep visible bands + the most-recently-used
        up to the cap. Pixels only; heights/geometry are retained."""
        if self._cap is None:
            victims = [k for k, cb in self._blocks.items() if not self._band_in(k, cb, lo, hi)]
        else:
            victims = self._overflow_victims(lo, hi)
        for k in victims:
            del self._blocks[k]
            self._bgra.pop(k, None)  # drop the fast-path memo alongside the pixels
        if victims and otel_metrics.block_cache_evictions is not None:
            otel_metrics.block_cache_evictions.add(len(victims))

    def _band_in(self, key: tuple[int, int], cb: CachedBlock, lo: int, hi: int) -> bool:
        """Does band ``key`` overlap the content window ``[lo, hi)``? False (evictable) for an unmeasured
        row that can't be placed."""
        top = self._band_top(key, cb)
        return top is not None and top < hi and top + cb.h > lo

    def _overflow_victims(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """LRU bands to drop back to ``self._cap`` — oldest first, never a currently-visible band."""
        assert self._cap is not None  # only called from the capped branch of _evict
        victims: list[tuple[int, int]] = []
        for k, cb in list(self._blocks.items()):  # oldest first
            if len(self._blocks) - len(victims) <= self._cap:
                break
            if not self._band_in(k, cb, lo, hi):
                victims.append(k)
        return victims

    def _composite_bands(
        self, table: OffsetTable, start: int, end: int, scroll: int, view_h: int
    ) -> Image.Image:
        """Assemble the viewport from the visible rows' bands: place each band at its absolute top and
        clip to the viewport. A band is a shorter block at a within-row offset, so :func:`composite_window`
        handles it unchanged — over an ephemeral per-band offset table (bands tile each row seam-exactly,
        gaps only between rows), giving pixel-identity with a one-shot ``render_panel`` crop."""
        starts: list[int] = []
        ends: list[int] = []
        blocks: list[tuple[int, Image.Image] | None] = []
        for i in range(start, end):
            if not self._offsets.known(i):
                continue
            row_top = table.starts[i]
            for b, y0, y1 in _row_bands(self._offsets.height(i)):
                cb = self._blocks.get((i, b))
                starts.append(row_top + y0)
                ends.append(row_top + y1)
                blocks.append((cb.x, cb.image()) if cb is not None else None)
        band_table = OffsetTable(tuple(starts), tuple(ends), table.top_pad, table.bottom_pad)
        return composite_window(
            blocks, band_table, scroll, view_h, width=self.width, background=self.theme.bg
        )

    def _band_bgra(self, key: tuple[int, int], cb: CachedBlock) -> np.ndarray:
        """The band as a full-width opaque premul-BGRA array — composited over bg at its ``x`` (→ opaque,
        so an overwrite-copy is exact), converted once and memoised (#138). Call under ``self._lock``."""
        from overlay.bgra import to_bgra_array

        arr = self._bgra.get(key)
        if arr is None:
            if otel_metrics.bgra_memo_misses is not None:
                otel_metrics.bgra_memo_misses.add(1)
            canvas = Image.new("RGBA", (self.width, cb.h), self.theme.bg)
            canvas.alpha_composite(cb.image(), (cb.x, 0))
            arr = to_bgra_array(canvas)
            self._bgra[key] = arr
        elif otel_metrics.bgra_memo_hits is not None:
            otel_metrics.bgra_memo_hits.add(1)
        return arr

    def _assemble_bgra(
        self, table: OffsetTable, start: int, end: int, scroll: int, view_h: int
    ) -> np.ndarray:
        """Assemble the viewport as premul-BGRA by disjoint numpy row-copies of the visible bands over a
        bg fill — byte-identical to ``to_bgra_array(self._composite_bands(...))`` (bands opaque over the
        same bg; proven in ``tests/test_banded_composite.py`` + the equivalence test)."""
        from overlay.bgra import to_bgra_array

        bg = self._bg_bgra
        if bg is None:
            bg = to_bgra_array(Image.new("RGBA", (1, 1), self.theme.bg))[0, 0]
            self._bg_bgra = bg
        out = np.empty((max(view_h, 1), self.width, 4), np.uint8)
        out[:] = bg
        for i in range(start, end):
            if not self._offsets.known(i):
                continue
            row_top = table.starts[i]
            for b, y0, _y1 in _row_bands(self._offsets.height(i)):
                cb = self._blocks.get((i, b))
                if cb is None:
                    continue
                band_top = row_top + y0 - scroll  # in viewport space (may be off either edge)
                src_y0 = max(0, -band_top)
                dst_y = max(0, band_top)
                h = min(cb.h - src_y0, view_h - dst_y)  # band rows inside the viewport
                if h <= 0:
                    continue
                out[dst_y : dst_y + h] = self._band_bgra((i, b), cb)[src_y0 : src_y0 + h]
        return out

    def viewport_bgra(self, scroll: int, view_h: int, overscan: int = 0) -> np.ndarray:
        """The viewport as premul-BGRA via per-band BGRA row-copies (#138) — no per-frame whole-viewport
        convert. Byte-identical to ``to_bgra_array(self.viewport(...))``; RGBA :meth:`viewport` stays for
        goldens/skeleton."""
        with self._lock:
            self._grow_prefix(scroll + view_h + overscan)
            table = self._offsets.estimated_table()
            start, end = table.visible_range(scroll, view_h, overscan)
            lo, hi = scroll - overscan, scroll + view_h + overscan
            for i in range(start, end):
                row_top = table.starts[i]
                self._ensure_bands(i, lo - row_top, hi - row_top)  # re-raster evicted bands in view
            out = self._assemble_bgra(table, start, end, scroll, view_h)
            self._evict(lo, hi)
            return out

    def viewport(self, scroll: int, view_h: int, overscan: int = 0) -> Image.Image:
        """Composite the ``[scroll, scroll+view_h)`` viewport, rendering + evicting BANDS as needed — a
        cold reach or warm scroll frame touches O(band) getmask2, never O(block)."""
        with self._lock:
            self._grow_prefix(scroll + view_h + overscan)
            table = self._offsets.estimated_table()  # exact for the measured prefix
            start, end = table.visible_range(scroll, view_h, overscan)
            lo, hi = scroll - overscan, scroll + view_h + overscan
            for i in range(start, end):
                row_top = table.starts[i]
                self._ensure_bands(i, lo - row_top, hi - row_top)  # re-raster evicted bands in view
            img = self._composite_bands(table, start, end, scroll, view_h)
            self._evict(lo, hi)
            return img

    def render_ahead(
        self,
        scroll: int,
        view_h: int,
        *,
        direction: int = 1,
        overscan: int = 0,
        max_blocks: int = 4,
        workers: int = 4,
        should_cancel: Callable[[], bool] | None = None,
    ) -> int:
        """Pre-render up to ``max_blocks`` BANDS just beyond the visible window in the scroll
        ``direction`` (+1 down / -1 up) — one screen of overscan — so a subsequent scroll composites
        them with no hot-path render. A band is ~9ms, warmable within the ~0.16s flick lead (the whole
        point of banding: the worker keeps ahead where it couldn't on a ~500ms whole block).

        First MEASURES ahead (grows the offset prefix past the viewport WITHOUT caching non-body pixels)
        so a row's first band never pays the ~200ms walk synchronously, then warms the ahead bands.

        Runs on the process-wide shared pool (:func:`overlay.parallel.shared_executor`, sized
        ``workers``): free-threaded → threads calling ``Row.render_window`` (the memoised layout is
        walk-once, each band cached the instant it lands via ``as_completed`` → progressive paint). A
        GIL build submits the picklable :func:`overlay.body_block.render_body_band` per band to a process
        pool (threads there serialise for worse than serial). ``should_cancel`` is checked between
        completions and cancels not-yet-started renders. Returns how many bands rendered."""
        with self._lock:
            targets = self._band_targets(scroll, view_h, overscan, direction, max_blocks)
        if not targets:
            return 0
        if workers > 1 and len(targets) > 1:
            return self._render_ahead_parallel(targets, workers, should_cancel)
        rendered = 0
        for i, b, y0, y1 in targets:
            if should_cancel is not None and should_cancel():
                break
            with self._lock:
                if (i, b) not in self._blocks:
                    self._store(self._render_band(i, b, y0, y1))
            rendered += 1
        return rendered

    def _band_targets(
        self, scroll: int, view_h: int, overscan: int, direction: int, max_blocks: int
    ) -> list[tuple[int, int, int, int]]:
        """The next up-to-``max_blocks`` not-yet-cached BODY bands just beyond the viewport in the scroll
        ``direction``. MEASURES ahead as it walks (``cache_nonbody=False`` — learn a row's height/walk
        without caching its non-body pixels), so a row's first band never pays the ~200ms walk on the
        main thread. Only banded body rows are targets (picklable ``body_args`` → pool path needs no
        cheap-inline split); the measure pass drops non-body pixels, so those small rows re-render on
        demand when the viewport reaches them — never on this warm path."""
        targets: list[tuple[int, int, int, int]] = []
        if direction >= 0:
            threshold = scroll + view_h + overscan  # first content row below the fold
            for i in range(len(self._rows)):
                if len(targets) >= max_blocks:
                    break
                self._ensure_measured(
                    i, cache_nonbody=False
                )  # measure/walk ahead off the main thread
                self._collect_bands_below(i, threshold, targets, max_blocks)
        else:
            threshold = (
                scroll - overscan
            )  # first content row above the fold (rows above are measured)
            for i in reversed(range(self._offsets.prefix_len)):
                if len(targets) >= max_blocks:
                    break
                self._collect_bands_above(i, threshold, targets, max_blocks)
        return targets

    def _collect_bands_below(
        self, i: int, threshold: int, targets: list[tuple[int, int, int, int]], max_blocks: int
    ) -> None:
        row = self._rows[i]
        if row.render_window is None:
            return
        row_top, h = self._offsets.start(i), self._offsets.height(i)
        for b, y0, y1 in _row_bands(h):
            if row_top + y1 <= threshold or (i, b) in self._blocks:
                continue
            targets.append((i, b, y0, y1))
            if len(targets) >= max_blocks:
                return

    def _collect_bands_above(
        self, i: int, threshold: int, targets: list[tuple[int, int, int, int]], max_blocks: int
    ) -> None:
        row = self._rows[i]
        if row.render_window is None:
            return
        row_top, h = self._offsets.start(i), self._offsets.height(i)
        for b, y0, y1 in reversed(_row_bands(h)):
            if row_top + y0 >= threshold or (i, b) in self._blocks:
                continue
            targets.append((i, b, y0, y1))
            if len(targets) >= max_blocks:
                return

    def _submit_process_pool(
        self, ex, targets: list[tuple[int, int, int, int]]
    ) -> dict[Future[Any], tuple[int, int, int, int]]:
        """Submit every band target to the process pool via the INJECTED picklable band renderer
        (``render_block_fn`` = ``overlay.body_block.render_body_band`` — plain ``BodyRenderArgs`` + band
        bounds; re-walks per call, but the GIL build has no shared-memory handle to reuse anyway). It is
        injected, not imported, to keep ``render`` free of the ``render → body_block`` import cycle. Only
        reached when ``render_block_fn`` is truthy (the None gate ran in the caller). All targets are
        body bands, so there is no cheap-inline split as the whole-row path once had."""
        fn = self._render_block_fn
        assert fn is not None  # caller gated on this (None → in-process serial)
        futures: dict[Future[Any], tuple[int, int, int, int]] = {}
        for tgt in targets:
            i, _b, y0, y1 = tgt
            body_args = self._rows[i].body_args
            assert body_args is not None  # banded rows always carry body_args
            futures[ex.submit(fn, body_args, y0, y1)] = tgt
        return futures

    def _render_serial(
        self, todo: list[tuple[int, int, int, int]], should_cancel: Callable[[], bool] | None
    ) -> int:
        """In-process serial band warm — the GIL-build path when no pool renderer is wired (hermetic
        tests inject ``render_block_fn=None``): a GIL thread pool would only serialise for worse."""
        rendered = 0
        for i, b, y0, y1 in todo:
            if should_cancel is not None and should_cancel():
                break
            with self._lock:
                if (i, b) not in self._blocks:
                    self._store(self._render_band(i, b, y0, y1))
            rendered += 1
        return rendered

    def _render_ahead_parallel(
        self,
        targets: list[tuple[int, int, int, int]],
        workers: int,
        should_cancel: Callable[[], bool] | None,
    ) -> int:
        """Render ``targets``'s not-yet-cached bands on the shared pool, caching each as it completes.

        Dispatch is decided from the ACTUAL executor :func:`~overlay.parallel.shared_executor` hands
        back (``isinstance(ex, ThreadPoolExecutor)``), not a second independent ``is_free_threaded()``
        call — the two must never disagree (a bound method submitted to a real process pool dies with an
        opaque ``PicklingError``), and deriving it from the object in hand makes that structurally
        impossible instead of merely unlikely."""
        with self._lock:
            todo = [t for t in targets if (t[0], t[1]) not in self._blocks]
        if not todo:
            return 0
        ex = shared_executor(workers)
        threaded = isinstance(ex, ThreadPoolExecutor)
        if not threaded and self._render_block_fn is None:
            return self._render_serial(todo, should_cancel)  # no pool wired → hermetic in-process
        # Future's result type differs by branch (RenderedBlock vs. render_body_band's raw 3-tuple)
        # but both land in the SAME dict for one unified as_completed() loop below (branched again
        # there via `threaded`) — Any is the honest type, not a mypy workaround.
        futures: dict[Future[Any], tuple[int, int, int, int]]
        if threaded:
            futures = {
                ex.submit(self._render_band, i, b, y0, y1): (i, b, y0, y1) for i, b, y0, y1 in todo
            }
        else:
            futures = self._submit_process_pool(ex, todo)
        rendered = 0
        for fut in as_completed(futures):
            if should_cancel is not None and should_cancel():
                for f in futures:
                    f.cancel()  # drop not-yet-started renders; in-flight ones finish and are ignored
                break
            i, b, y0, _y1 = futures[fut]
            if threaded:
                with self._lock:
                    self._store(fut.result())  # progressive: cache each band as it lands
            else:
                img, scan, links = fut.result()
                with self._lock:
                    self._store(RenderedBlock(i, b, self._rows[i].x, y0, img, scan, links))
            rendered += 1
        return rendered

    def skeleton_frame(
        self, scroll: int, view_h: int, overscan: int = 0, *, skeleton: RGBA = (214, 214, 214, 255)
    ) -> Image.Image:
        """A NON-BLOCKING full viewport frame: composite the bands whose pixels are already cached and
        draw a solid ``skeleton`` band for any visible region not yet rendered (a missing band, or an
        unmeasured row's whole estimated extent). Always the full viewport size (atomic — never a partial
        frame); converges to the exact :meth:`viewport` once ``render_ahead`` fills the missing bands."""
        with self._lock:
            table = self._offsets.estimated_table()
            start, end = table.visible_range(scroll, view_h, overscan)
            img = self._composite_bands(table, start, end, scroll, view_h)
            missing = self._missing_regions(table, start, end)
        draw = ImageDraw.Draw(img)
        m = self.theme.margin
        for block_top, block_end in missing:
            y0 = max(0, block_top - scroll)
            y1 = min(view_h, block_end - scroll)
            if y1 > y0:
                draw.rectangle((m, y0, self.width - m - 1, y1 - 1), fill=skeleton)
        return img

    def _missing_regions(self, table: OffsetTable, start: int, end: int) -> list[tuple[int, int]]:
        """Content-space ``[top, end)`` spans in the visible rows with no cached pixels — an unmeasured
        row's whole estimated extent, or a measured row's not-yet-rendered bands."""
        out: list[tuple[int, int]] = []
        for i in range(start, end):
            row_top = table.starts[i]
            if not self._offsets.known(i):
                out.append((row_top, table.ends[i]))  # unmeasured — skeleton the whole estimate
                continue
            for _b, y0, y1 in _row_bands(self._offsets.height(i)):
                if (i, _b) not in self._blocks:
                    out.append((row_top + y0, row_top + y1))
        return out

    def scan_boxes(self) -> list[ScanBox]:
        """Panel-space :class:`ScanBox`es of every block ever rendered — retained past pixel eviction,
        so scrolling back over an evicted block still hovers. Ordered by block, then by cell."""
        from overlay.model import ScanBox

        out: list[ScanBox] = []
        with self._lock:
            for i in sorted(self._geom):
                g = self._geom[i]
                top = self._offsets.start(i)
                out.extend(ScanBox(sb.text, sb.x + g.x, sb.y + top, sb.w, sb.h) for sb in g.scan)
        return out

    def link_boxes(self) -> list[LinkBox]:
        """Panel-space :class:`LinkBox`es of every block ever rendered (retained past pixel eviction)."""
        from overlay.model import LinkBox

        out: list[LinkBox] = []
        with self._lock:
            for i in sorted(self._geom):
                g = self._geom[i]
                top = self._offsets.start(i)
                out.extend(LinkBox(lb.query, lb.x + g.x, lb.y + top, lb.w, lb.h) for lb in g.links)
        return out

    def scan_hit(self, content_x: int, content_y: int) -> ScanBox | None:
        """The panel-space :class:`ScanBox` under a content-space point, or ``None``. Binary-searches
        the block by ``content_y`` (offset table), then tests that block's retained cell rects — so it
        resolves even when the block's pixels are evicted. The controller passes
        ``content = (mx - sx, my - sy + scroll)``."""
        from overlay.model import ScanBox

        hit = self._hit_block(content_y)
        if hit is None:
            return None
        g, top = hit
        for sb in g.scan:
            bx, by = sb.x + g.x, sb.y + top
            if bx <= content_x < bx + sb.w and by <= content_y < by + sb.h:
                return ScanBox(sb.text, bx, by, sb.w, sb.h)
        return None

    def link_hit(self, content_x: int, content_y: int) -> LinkBox | None:
        """The panel-space :class:`LinkBox` under a content-space point, or ``None`` (see
        :meth:`scan_hit`)."""
        from overlay.model import LinkBox

        hit = self._hit_block(content_y)
        if hit is None:
            return None
        g, top = hit
        for lb in g.links:
            bx, by = lb.x + g.x, lb.y + top
            if bx <= content_x < bx + lb.w and by <= content_y < by + lb.h:
                return LinkBox(lb.query, bx, by, lb.w, lb.h)
        return None

    def _hit_block(self, content_y: int) -> tuple[BlockGeom, int] | None:
        """The retained block containing ``content_y`` and its panel-space top, or ``None`` (in a
        gap/margin, or over a not-yet-rendered block whose geometry is unknown)."""
        with self._lock:
            i = self._offsets.estimated_table().block_at(content_y)
            if i is None or i not in self._geom:
                return None
            return (self._geom[i], self._offsets.start(i))
