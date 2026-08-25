"""Compose deferred rows into complete or viewport-first panel images."""

from __future__ import annotations

import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from PIL import Image

from saitenka.model import _DEFAULT_THEME, LinkBox, ScanBox, Theme
from saitenka.panel.body import render_body_block
from saitenka.panel.rows import Row, panel_rows
from saitenka.parallel import shared_executor

if TYPE_CHECKING:
    from saitenka.panel.model import Entry


def compose_panel(
    rendered: list[tuple[int, Image.Image]],
    width: int,
    theme: Theme = _DEFAULT_THEME,
    gaps: list[int] | None = None,
    top_reserve: int = 0,
) -> Image.Image:
    """Stack already-rendered ``(x, image)`` rows into one canvas (the geometry ``render_panel`` uses).

    ``gaps[i]`` is the gap placed *after* row ``i`` (defaults to a uniform ``theme.gap``); only the
    ``n-1`` inter-row gaps add to the height. ``top_reserve`` leaves that many blank pixels above the
    first row — used to clear the sticky dict-tab strip so it never overlaps the header/reading."""
    m = theme.margin
    n = len(rendered)
    if gaps is None:
        gaps = [theme.gap] * n
    inter = sum(gaps[i] for i in range(n - 1)) if n > 1 else 0
    total_h = 2 * m + top_reserve + sum(im.height for _, im in rendered) + inter
    canvas = Image.new("RGBA", (width, max(total_h, 1)), theme.bg)
    y = m + top_reserve
    for i, (x, im) in enumerate(rendered):
        canvas.alpha_composite(im, (x, y))
        y += im.height + (gaps[i] if i < n - 1 else 0)
    return canvas


class LazyPanel:
    """Row-by-row, viewport-first panel. ``render_to(h)`` renders just enough rows to cover ``h`` px
    and composes them; ``finish()`` renders the rest. A cold 6-dict tooltip paints its visible top
    immediately and streams the below-the-fold bodies in afterwards, instead of blocking ~860 ms."""

    def __init__(
        self, rows: list[Row], width: int, theme: Theme = _DEFAULT_THEME, top_reserve: int = 0
    ):
        self.top_reserve = top_reserve  # blank px above row 0 to clear the sticky tab strip
        self._pending = list(rows)  # unrendered thunks (popped front-to-back)
        self._rendered: list[tuple[int, Image.Image, list[ScanBox], list[LinkBox], int]] = []
        # Bounded strip of the FIRST pending row, shown in the head compose only. The row itself
        # stays pending — finish() re-renders it fully, so the completed panel is unchanged.
        self._partial: tuple[int, Image.Image, list[ScanBox], list[LinkBox], int] | None = None
        self.width = width
        self.theme = theme
        self._row_sections: list[str | None] = []  # parallel to _rendered (dict-tab sections)
        self.scan_boxes: list[ScanBox] = []  # panel-space hitboxes for the rendered rows
        self.link_boxes: list[LinkBox] = []  # panel-space clickable link regions
        self._offsets_frozen: list[tuple[str, int]] | None = None  # cached at release_rows()
        # render_to() is called from both the main-thread hover path and a prefetch worker on the
        # same panel key (popups.py's "single-writer per key" assumption isn't airtight — a re-hover
        # can race a still-running finish()); guards _pending/_rendered/_partial against concurrent pop.
        self._lock = threading.Lock()

    @property
    def complete(self) -> bool:
        return not self._pending

    def _height(self) -> int:
        n = len(self._rendered)
        if n == 0:
            return 0
        m = self.theme.margin
        heights = sum(r[1].height for r in self._rendered)
        inter = sum(self._rendered[i][4] for i in range(n - 1)) if n > 1 else 0
        return 2 * m + self.top_reserve + heights + inter

    def _compose(self) -> Image.Image:
        m = self.theme.margin
        show = self._rendered + ([self._partial] if self._partial is not None else [])
        canvas = compose_panel(
            [(x, im) for x, im, _, _, _ in show],
            self.width,
            self.theme,
            gaps=[g for *_, g in show],
            top_reserve=self.top_reserve,
        )
        scan: list[ScanBox] = []
        links: list[LinkBox] = []
        y = m + self.top_reserve
        n = len(show)
        for i, (x, im, local, llocal, g) in enumerate(show):
            # row-local → panel coords
            scan.extend(ScanBox(sb.text, sb.x + x, sb.y + y, sb.w, sb.h) for sb in local)
            links.extend(LinkBox(lb.query, lb.x + x, lb.y + y, lb.w, lb.h) for lb in llocal)
            y += im.height + (g if i < n - 1 else 0)
        self.scan_boxes = scan
        self.link_boxes = links
        return canvas

    def release_rows(self) -> None:
        """Drop the per-row rendered sub-images once the panel is complete and its BGRA has been
        captured elsewhere — they are the single largest retained buffer (a full second copy of the
        panel) and are never needed again: scrolling slices the BGRA, hit-testing uses ``scan_boxes`` /
        ``link_boxes`` (already composed onto ``self``), and the only other reader — ``section_offsets``
        — is frozen here first. Idempotent."""
        if self._offsets_frozen is None:
            self._offsets_frozen = self.section_offsets()
        self._rendered = []
        self._row_sections = []
        self._partial = None

    def section_offsets(self) -> list[tuple[str, int]]:
        """(dict_name, y) for each rendered section-start row, in panel coords — the scroll targets
        for the tab row and LEFT/RIGHT keyboard nav. Grows as finish() streams, then frozen by
        release_rows() so it survives dropping the row images."""
        if self._offsets_frozen is not None:
            return self._offsets_frozen
        m = self.theme.margin
        y = m + self.top_reserve
        out: list[tuple[str, int]] = []
        n = len(self._rendered)
        for i, ((_x, im, _s, _l, g), sec) in enumerate(
            zip(self._rendered, self._row_sections, strict=True)
        ):
            if sec:
                out.append((sec, y))
            y += im.height + (g if i < n - 1 else 0)
        return out

    def render_to(self, min_height: int) -> Image.Image:
        """Render rows until the composed panel is at least ``min_height`` px tall (or all rows are
        done), then compose. Safe for concurrent callers (lock-serialized) — each renders what's left.

        If the next row supports bounded raster (a def-body block) and the remaining budget is
        smaller than the row, only the covering strip is rasterised now and the row stays pending
        — cold first paint is O(viewport) even when the first def body is one enormous block."""
        with self._lock:
            return self._render_to_locked(min_height)

    def _render_to_locked(self, min_height: int) -> Image.Image:
        self._partial = None
        while self._pending and self._height() < min_height:
            row = self._pending[0]
            gap = row.gap if row.gap is not None else self.theme.gap
            if row.render_capped is not None:
                remaining = min_height - self._height()
                img, scan, links, complete = row.render_capped(remaining)
                if not complete:
                    self._partial = (row.x, img, scan, links, gap)  # head strip; stays pending
                    break
                self._pending.pop(0)
                self._rendered.append((row.x, img, scan, links, gap))
                self._row_sections.append(row.section)
                continue
            self._pending.pop(0)
            img, scan, links = row.render()
            self._rendered.append((row.x, img, scan, links, gap))
            self._row_sections.append(row.section)
        return self._compose()

    def finish(self, workers: int = 4) -> Image.Image:
        """Render every remaining row and compose the complete panel. Gated on the count of
        *poolable* rows (``body_args`` set — the FreeType-bound def bodies, the only expensive
        work here), not raw row count: a typical 1-2-dict panel's header/chip rows are microseconds
        each and not worth dispatch overhead. ``>= 2`` poolable rows fans out to the process-wide
        :func:`~saitenka.parallel.shared_executor` (see :meth:`_finish_parallel`) instead of
        rendering serially on the calling thread — the same pool
        :meth:`~saitenka.render.banded.WindowedPanel.render_ahead` already uses for scroll-ahead
        blocks. This is what tooltip prefetch's engaged (``full=True``) worker jobs land in: a
        several-second multi-dict ``finish()`` no longer burns entirely on one prefetch thread."""
        with self._lock:
            pending = list(self._pending)
            poolable = sum(1 for row in pending if row.body_args is not None)
            if poolable <= 1:
                return self._render_to_locked(1 << 30)
            return self._finish_parallel(pending, workers)

    def _finish_parallel(self, pending: list[Row], workers: int) -> Image.Image:
        """Render ``pending`` (>1 poolable rows, all currently pending — called with ``self._lock``
        held, so no other renderer can race ``_pending``/``_rendered``) concurrently: threads on a
        free-threaded build (row thunks are closures, fine for threads, zero copy), a process pool on
        a GIL build for the picklable ``body_args`` rows only — cheap header/freq/reading rows aren't
        FreeType-bound and aren't picklable, so they still render inline. Mirrors
        ``WindowedPanel._render_ahead_parallel``'s dispatch."""
        self._partial = None
        ex = shared_executor(workers)
        results = (
            self._finish_threaded(ex, pending)
            if isinstance(ex, ThreadPoolExecutor)
            else self._finish_pooled(ex, pending)
        )
        for row, result in zip(pending, results, strict=True):
            assert result is not None
            img, scan, links = result
            gap = row.gap if row.gap is not None else self.theme.gap
            self._rendered.append((row.x, img, scan, links, gap))
            self._row_sections.append(row.section)
        self._pending = []
        return self._compose()

    @staticmethod
    def _finish_threaded(
        ex: ThreadPoolExecutor, pending: list[Row]
    ) -> list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None]:
        results: list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None] = [None] * len(
            pending
        )
        thread_futures: dict[Future[tuple[Image.Image, list[ScanBox], list[LinkBox]]], int] = {
            ex.submit(row.render): i for i, row in enumerate(pending)
        }
        for fut in as_completed(thread_futures):
            results[thread_futures[fut]] = fut.result()
        return results

    @staticmethod
    def _finish_pooled(
        ex: Executor, pending: list[Row]
    ) -> list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None]:
        results: list[tuple[Image.Image, list[ScanBox], list[LinkBox]] | None] = [None] * len(
            pending
        )
        pool_futures: dict[Future[tuple[Image.Image, list[ScanBox], list[LinkBox], bool]], int] = {}
        for i, row in enumerate(pending):
            if row.body_args is not None:
                pool_futures[ex.submit(render_body_block, row.body_args)] = i
            else:
                results[i] = row.render()
        for pfut in as_completed(pool_futures):
            img, scan, links, _complete = pfut.result()
            results[pool_futures[pfut]] = (img, scan, links)
        return results


def render_panel(
    entry: Entry,
    width: int = 384,
    theme: Theme = _DEFAULT_THEME,
    max_height: int | None = None,
    scroll_y: int = 0,
    *,
    add_button: bool = False,
    mined: bool = False,
    group_mined: tuple[bool, ...] = (),
) -> Image.Image:
    rows = panel_rows(
        entry, width, theme, add_button=add_button, mined=mined, group_mined=group_mined
    )
    rendered = [(r.x, r.render()[0]) for r in rows]
    gaps = [theme.gap if r.gap is None else r.gap for r in rows]
    canvas = compose_panel(rendered, width, theme, gaps)
    total_h = canvas.height

    if max_height is not None and total_h > max_height:
        # clip to a viewport (scroll offset now; scrollbar drawn by the controller viewport)
        top = max(0, min(scroll_y, total_h - max_height))
        canvas = canvas.crop((0, top, width, top + max_height))
    return canvas
