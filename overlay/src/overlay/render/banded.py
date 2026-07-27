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

from PIL import Image, ImageDraw

from overlay.parallel import shared_executor

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Future
    from typing import Any

    from overlay.body_block import BodyRenderArgs
    from overlay.model import RGBA, LinkBox, ScanBox, Theme
    from overlay.panel import Row
    from overlay.render.window import OffsetTable

# The def-body block renderer (overlay.body_block.render_body_block) is injected by the caller
# (app/tooltip.py, which builds WindowedPanel) rather than imported here: body_block.py itself
# depends on render.document, so a module-level import here would cycle back through .render at the
# package level. See WindowedPanel's render_block_fn param.


@dataclass(frozen=True, slots=True)
class CachedBlock:
    """A rendered block held in the pixel cache: its x-offset, row-local scan/link hitboxes, and the
    image kept either live or (to shrink retained memory, as the whole-panel path does today)
    zlib-compressed. Heights are NOT stored here — they live in the offset table and are retained past
    pixel eviction, so offsets stay exact for every block ever measured."""

    x: int
    scan: list[ScanBox]
    links: list[LinkBox]
    _img: Image.Image | None = None
    _packed: tuple[bytes, tuple[int, int]] | None = None  # (zlib(rgba bytes), (w, h))

    @classmethod
    def make(cls, x: int, img: Image.Image, scan, links, *, compress: bool) -> CachedBlock:
        if compress:
            rgba = img.convert("RGBA")
            packed = (zlib.compress(rgba.tobytes(), 1), rgba.size)
            return cls(x, scan, links, _packed=packed)
        return cls(x, scan, links, _img=img)

    def image(self) -> Image.Image:
        if self._img is not None:
            return self._img
        assert self._packed is not None
        data, size = self._packed
        return Image.frombytes("RGBA", size, zlib.decompress(data))


@dataclass(frozen=True, slots=True)
class BlockGeom:
    """A block's retained hit geometry: its x-offset and row-local scan/link boxes. Kept even after the
    block's pixels are evicted, so a hover over a scrolled-away block still resolves."""

    x: int
    scan: list[ScanBox]
    links: list[LinkBox]


@dataclass(frozen=True, slots=True)
class RenderedBlock:
    """A freshly rendered block before it enters the cache — the value a worker thread returns."""

    index: int
    x: int
    image: Image.Image
    scan: list[ScanBox]
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
    """Drive :func:`overlay.panel.panel_rows` from the geometry core: render a block only when the
    visible range needs it, **retain each visited block's exact height even after its pixels are
    evicted**, and composite viewports with :func:`composite_window`.

    Each row is one block; its trailing gap is known at build time (``Row.gap``), its height only once
    rendered. Building the panel walks no content (the deferred-thunk contract). Reaching scroll ``S``
    renders every block above ``S`` exactly once to learn its height — so incremental scroll offsets
    stay exact — then evicts the pixels of blocks outside the viewport±overscan while keeping their
    heights, bounding retained pixels to O(viewport). Byte-for-byte identical to cropping a one-shot
    :func:`overlay.panel.render_panel` at the same offset (proven in ``tests/test_windowed_panel.py``)."""

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
        render_block_fn: Callable[[BodyRenderArgs, int | None], tuple] | None = None,
    ):
        from overlay.model import _DEFAULT_THEME
        from overlay.render.window import LazyOffsets

        self.theme = theme if theme is not None else _DEFAULT_THEME
        self.width = width
        self.top_reserve = top_reserve
        self._rows = list(rows)
        self._cap = max_cached_blocks  # LRU pixel-cache cap (None = keep exactly visible±overscan)
        self._compress = compress
        # Injected, not imported: overlay.body_block depends on render.document, so a module-level
        # import of render_body_block here would cycle back through .render at the package level.
        # Only needed for the GIL-build process-pool path in _render_ahead_parallel.
        self._render_block_fn = render_block_fn
        gaps = [r.gap if r.gap is not None else self.theme.gap for r in self._rows]
        self._offsets = LazyOffsets(
            gaps, self.theme.margin + top_reserve, self.theme.margin, seed_height=seed_height
        )
        # LRU pixel cache (oldest first). Heights live in self._offsets and are never dropped, so
        # offsets stay exact for every block ever rendered even after its pixels are evicted here.
        self._blocks: OrderedDict[int, CachedBlock] = OrderedDict()
        # Retained per-block hit geometry, NEVER evicted — so a hover resolves even when the block's
        # pixels are gone (scrolled back over an evicted block).
        self._geom: dict[int, BlockGeom] = {}
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
    def cached_blocks(self) -> int:
        """Blocks whose pixels are currently retained (bounded by ``max_cached_blocks`` if set, else by
        the viewport±overscan window)."""
        return len(self._blocks)

    def _store(self, rb: RenderedBlock) -> CachedBlock:
        """Cache a rendered block's pixels + geometry + height. Call under ``self._lock``. Idempotent:
        ``set_height`` is deterministic and re-storing just overwrites/re-touches the LRU entry."""
        self._offsets.set_height(rb.index, rb.image.height)
        self._geom[rb.index] = BlockGeom(
            rb.x, rb.scan, rb.links
        )  # retained past pixel eviction (hits)
        block = CachedBlock.make(rb.x, rb.image, rb.scan, rb.links, compress=self._compress)
        self._blocks[rb.index] = block
        self._blocks.move_to_end(rb.index)  # LRU touch
        return block

    def _render_pixels(self, i: int) -> RenderedBlock:
        """Render row ``i`` WITHOUT touching shared state — safe to run on a worker thread (fonts are
        thread-local; each row renders its own content). The caller stores the result under the lock."""
        row = self._rows[i]
        img, scan, links = row.render()
        return RenderedBlock(i, row.x, img, scan, links)

    def _ensure_block(self, i: int) -> CachedBlock:
        block = self._blocks.get(i)
        if block is None:
            block = self._store(self._render_pixels(i))
        else:
            self._blocks.move_to_end(i)  # LRU touch
        return block

    def _grow_prefix(self, target_y: int) -> None:
        """Render blocks forward (top-down) until the exact-offset prefix covers ``target_y`` px."""
        i = self._offsets.prefix_len
        while i < len(self._rows) and self._offsets.start(i) < target_y:
            self._ensure_block(i)
            i += 1

    def _evict(self, keep_start: int, keep_end: int) -> None:
        """Bound the retained pixel set. With no cap, drop everything outside ``[keep_start, keep_end)``
        (the viewport±overscan). With a cap, keep the visible window plus the most-recently-used blocks
        up to the cap — evicting LRU beyond it, never a currently-visible block — so a small scroll
        reversal reuses a cached block instead of re-rendering it. Pixels only; heights are retained."""
        if self._cap is None:
            for i in [i for i in self._blocks if not keep_start <= i < keep_end]:
                del self._blocks[i]
            return
        for i in list(self._blocks):  # oldest first
            if len(self._blocks) <= self._cap:
                break
            if not keep_start <= i < keep_end:  # never evict a visible block
                del self._blocks[i]

    def viewport(self, scroll: int, view_h: int, overscan: int = 0) -> Image.Image:
        """Composite the ``[scroll, scroll+view_h)`` viewport, rendering + evicting blocks as needed."""
        with self._lock:
            self._grow_prefix(scroll + view_h + overscan)
            table = self._offsets.estimated_table()  # exact for the rendered prefix
            start, end = table.visible_range(scroll, view_h, overscan)
            for i in range(start, end):
                self._ensure_block(i)  # re-render an evicted block scrolled back into view
            blocks: list[tuple[int, Image.Image] | None] = [
                (b.x, b.image()) if (b := self._blocks.get(i)) is not None else None
                for i in range(len(self._rows))
            ]
            img = composite_window(
                blocks, table, scroll, view_h, width=self.width, background=self.theme.bg
            )
            self._evict(start, end)
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
        """Pre-render up to ``max_blocks`` blocks just beyond the visible window in the scroll
        ``direction`` (+1 down / -1 up), so a subsequent scroll composites them with no hot-path render.

        Runs on the process-wide shared pool (:func:`overlay.parallel.shared_executor`, sized
        ``workers``): on a free-threaded build that's threads — each block is cached the instant it
        lands (``as_completed`` → progressive paint) with zero copy, a shared-memory reference. On a
        GIL build threads would serialise for *worse* than serial (measured — pure overhead, zero
        parallelism), so this uses a process pool instead for the def-body blocks specifically
        (``Row.body_args`` — plain picklable data, see ``panel.BodyRenderArgs``/``render_body_block``);
        rows without ``body_args`` (header/freq/reading/def-head — cheap chip/flow layout, not
        FreeType-bound, and not picklable) render inline first, not worth parallelizing either way.
        ``should_cancel`` is checked between completions (mirrors the prefetch generation bump on a
        word switch) and cancels not-yet-started renders. Returns how many blocks rendered."""
        with self._lock:
            self._grow_prefix(scroll + view_h + overscan)
            table = self._offsets.estimated_table()
            start, end = table.visible_range(scroll, view_h, overscan)
        if direction >= 0:
            order = list(range(end, min(end + max_blocks, self.count)))
        else:
            order = list(range(start - 1, max(-1, start - 1 - max_blocks), -1))
        if workers > 1 and len(order) > 1:
            return self._render_ahead_parallel(order, workers, should_cancel)
        rendered = 0
        for i in order:
            if should_cancel is not None and should_cancel():
                break
            with self._lock:
                self._ensure_block(i)
            rendered += 1
        return rendered

    def _submit_process_pool(
        self, ex, todo: list[int], should_cancel: Callable[[], bool] | None
    ) -> tuple[dict[Future[Any], int], int]:
        """Cheap (non-poolable) rows render inline immediately; the rest submit to the process pool.
        Returns ``(futures, rendered_inline)`` — an empty ``futures`` means the caller is done (either
        cancelled mid-inline or nothing left to submit) and should return ``rendered_inline`` as-is."""
        rendered = 0
        fn = self._render_block_fn
        inline = (
            todo
            if fn is None  # no injected renderer (e.g. a caller that never set it) — degrade
            else [i for i in todo if self._rows[i].body_args is None]  # cheap rows: not
        )  # picklable, not FreeType-bound — just render them, whether or not a pool is available
        poolable = [] if fn is None else [i for i in todo if self._rows[i].body_args is not None]
        for i in inline:
            if should_cancel is not None and should_cancel():
                return {}, rendered
            with self._lock:
                self._store(self._render_pixels(i))
            rendered += 1
        if not poolable:
            return {}, rendered
        assert fn is not None  # poolable is only non-empty when fn is set
        futures: dict[Future[Any], int] = {}
        for i in poolable:
            body_args = self._rows[i].body_args
            assert body_args is not None  # poolable was filtered on this above
            futures[ex.submit(fn, body_args, None)] = i
        return futures, rendered

    def _render_ahead_parallel(
        self, order: list[int], workers: int, should_cancel: Callable[[], bool] | None
    ) -> int:
        """Render ``order``'s not-yet-cached blocks on the shared pool, caching each as it completes.

        Dispatch is decided from the ACTUAL executor :func:`~overlay.parallel.shared_executor` hands
        back (``isinstance(ex, ThreadPoolExecutor)``), not a second independent
        ``is_free_threaded()`` call — the two must never disagree (a bound method submitted to a real
        process pool dies with an opaque ``PicklingError``), and deriving it from the object in hand
        makes that structurally impossible instead of merely unlikely."""
        with self._lock:
            todo = [i for i in order if i not in self._blocks]
        if not todo:
            return 0
        ex = shared_executor(workers)
        threaded = isinstance(ex, ThreadPoolExecutor)
        # Future's result type differs by branch (RenderedBlock vs. render_body_block's raw 4-tuple)
        # but both land in the SAME dict for one unified as_completed() loop below (branched again
        # there via `threaded`) — Any is the honest type, not a mypy workaround.
        futures: dict[Future[Any], int]
        if threaded:
            rendered = 0
            futures = {ex.submit(self._render_pixels, i): i for i in todo}
        else:
            futures, rendered = self._submit_process_pool(ex, todo, should_cancel)
            if not futures:
                return rendered
        for fut in as_completed(futures):
            if should_cancel is not None and should_cancel():
                for f in futures:
                    f.cancel()  # drop not-yet-started renders; in-flight ones finish and are ignored
                break
            i = futures[fut]
            if threaded:
                with self._lock:
                    self._store(fut.result())  # progressive: cache each block as it lands
            else:
                img, scan, links, _complete = fut.result()
                row = self._rows[i]
                with self._lock:
                    self._store(RenderedBlock(i, row.x, img, scan, links))
            rendered += 1
        return rendered

    def skeleton_frame(
        self, scroll: int, view_h: int, overscan: int = 0, *, skeleton: RGBA = (214, 214, 214, 255)
    ) -> Image.Image:
        """A NON-BLOCKING full viewport frame: composite the blocks whose pixels are already cached and
        draw a solid ``skeleton`` band for any visible block not yet rendered. Always the full viewport
        size (atomic — never a partial frame); converges to the exact :meth:`viewport` once
        ``render_ahead`` fills the missing blocks. The skeleton→real swap is UX tuning, not correctness."""
        with self._lock:
            table = self._offsets.estimated_table()
            start, end = table.visible_range(scroll, view_h, overscan)
            blocks: list[tuple[int, Image.Image] | None] = [
                (b.x, b.image()) if (b := self._blocks.get(i)) is not None else None
                for i in range(len(self._rows))
            ]
            missing = [
                (table.starts[i], table.ends[i]) for i in range(start, end) if blocks[i] is None
            ]
        img = composite_window(
            blocks, table, scroll, view_h, width=self.width, background=self.theme.bg
        )
        if missing:
            draw = ImageDraw.Draw(img)
            m = self.theme.margin
            for block_top, block_end in missing:
                y0 = max(0, block_top - scroll)
                y1 = min(view_h, block_end - scroll)
                if y1 > y0:
                    draw.rectangle((m, y0, self.width - m - 1, y1 - 1), fill=skeleton)
        return img

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
