"""Pure block-geometry core for the windowed tooltip renderer — no PIL, all integer, all exact.

Two pieces the windowed engine is grown from:

- :class:`OffsetTable` — the cumulative start/end of every block from its height + the inter-block gaps
  + top/bottom margins (the exact geometry :func:`overlay.panel.compose_panel` lays out), plus a
  half-open visible-range kernel (``scroll + viewport + overscan → [start, end)``) and a
  ``content_y ↔ (block, local_y)`` round trip.
- :class:`LazyOffsets` — the partially-known variant: gaps are known at build time, block heights are
  filled in only as blocks are rendered. Offsets are **exact for the contiguous visited prefix** and
  **estimated below it** (a running average of visited heights). That estimate is the one unavoidable
  approximation — cosmetic only (scrollbar-thumb size / drag-to-absolute precision); it never changes
  which block an incremental wheel/arrow step lands on, and it converges to exact as the entry is
  scrolled. A visited block's offset never moves as later heights arrive.

Gap convention (matches ``compose_panel``): ``gaps[i]`` is the gap placed *after* block ``i``; only the
first ``n-1`` gaps add height (there is no gap after the last block). Callers pass a full-length
``gaps`` list; the trailing entry is ignored.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay.render.layout_backend import DEFAULT_BACKEND

if TYPE_CHECKING:
    from collections.abc import Sequence

    from overlay.render.layout_backend import LayoutBackend

# Row-stack geometry lives behind the LayoutBackend seam (#113): build_offsets takes a backend and
# LazyOffsets routes through its own; the default is byte-identical to the old cumulative arithmetic.


@dataclass(frozen=True, slots=True)
class OffsetTable:
    """Exact block offset table + the visible-range / hit kernels. All coordinates are content-space
    (panel) pixels, integer by construction."""

    starts: tuple[int, ...]
    ends: tuple[int, ...]
    top_pad: int
    bottom_pad: int

    @property
    def count(self) -> int:
        return len(self.starts)

    @property
    def total(self) -> int:
        """Full content height, including the top and bottom margins (matches ``compose_panel``)."""
        if not self.starts:
            return self.top_pad + self.bottom_pad
        return self.ends[-1] + self.bottom_pad

    def visible_range(self, scroll: int, viewport: int, overscan: int = 0) -> tuple[int, int]:
        """Half-open ``[start, end)`` of blocks intersecting ``[scroll, scroll+viewport)`` grown by
        ``overscan`` px on each side. A block is visible iff ``start < hi and end > lo`` where
        ``lo = scroll - overscan`` and ``hi = scroll + viewport + overscan``."""
        n = self.count
        if n == 0:
            return (0, 0)
        lo = scroll - overscan
        hi = scroll + viewport + overscan
        start = bisect_right(self.ends, lo)  # first block whose end > lo
        end = bisect_left(self.starts, hi)  # first block whose start >= hi
        return (
            start,
            max(start, end),
        )  # max(): a zero-height block on the boundary can't invert it

    def block_at(self, content_y: int) -> int | None:
        """Index of the block containing ``content_y`` (``start <= y < end``), or ``None`` in a gap or
        the top/bottom margin."""
        i = bisect_right(self.ends, content_y)  # first block whose end > content_y
        if i < self.count and self.starts[i] <= content_y < self.ends[i]:
            return i
        return None

    def local_y(self, content_y: int) -> tuple[int, int] | None:
        """``(block, local_y)`` for ``content_y`` (``local_y`` measured from the block's top), or
        ``None`` outside every block."""
        i = self.block_at(content_y)
        if i is None:
            return None
        return (i, content_y - self.starts[i])

    def content_y(self, block: int, local_y: int) -> int:
        """Inverse of :meth:`local_y` — the panel-space y of ``local_y`` within ``block``."""
        return self.starts[block] + local_y


def build_offsets(
    heights: Sequence[int],
    gaps: Sequence[int],
    top_pad: int,
    bottom_pad: int,
    backend: LayoutBackend = DEFAULT_BACKEND,
) -> OffsetTable:
    """Exact offset table from fully-known block heights + trailing gaps + margins, via ``backend``."""
    if len(gaps) != len(heights):
        raise ValueError("gaps must be per-block (one trailing gap per block; the last is ignored)")
    starts, ends = backend.cumulative(heights, gaps, top_pad)
    return OffsetTable(starts, ends, top_pad, bottom_pad)


class LazyOffsets:
    """Partially-known offset table. Gaps + margins are fixed at construction; heights arrive one block
    at a time via :meth:`set_height`. Offsets are exact for the contiguous visited prefix and estimated
    (running average of visited heights) below it."""

    def __init__(
        self,
        gaps: Sequence[int],
        top_pad: int,
        bottom_pad: int,
        *,
        seed_height: int = 0,
        backend: LayoutBackend = DEFAULT_BACKEND,
    ):
        self._gaps = tuple(gaps)
        self._n = len(self._gaps)
        self._top = top_pad
        self._bottom = bottom_pad
        self._heights: list[int | None] = [None] * self._n
        self._seed = seed_height  # placeholder height before any real block is measured
        self._backend = backend  # LayoutBackend seam (#113); default is byte-identical arithmetic

    @property
    def count(self) -> int:
        return self._n

    def set_height(self, i: int, height: int) -> None:
        if height < 0:
            raise ValueError("block height must be non-negative")
        self._heights[i] = height

    def known(self, i: int) -> bool:
        return self._heights[i] is not None

    def height(self, i: int) -> int:
        """The measured height of block ``i`` (raises if not yet measured) — the banded engine tiles a
        row into bands from it without recomputing the whole cumulative table."""
        h = self._heights[i]
        if h is None:
            raise ValueError(f"block {i} not measured")
        return h

    @property
    def prefix_len(self) -> int:
        """Number of leading blocks whose heights — hence exact offsets — are all known."""
        k = 0
        while k < self._n and self._heights[k] is not None:
            k += 1
        return k

    def start_exact(self, i: int) -> bool:
        """True iff :meth:`start` of block ``i`` is exact (every block before it is measured)."""
        return i <= self.prefix_len

    def _avg(self) -> float:
        known = [h for h in self._heights if h is not None]
        return sum(known) / len(known) if known else float(self._seed)

    def _fill(self, avg: float) -> list[int]:
        return [h if h is not None else round(avg) for h in self._heights]

    def start(self, i: int) -> int:
        """Start y of block ``i`` — exact when :meth:`start_exact`, else estimated. Depends only on
        blocks before ``i``, so a visited-prefix offset never moves as later heights arrive."""
        starts, _ = self._backend.cumulative(self._fill(self._avg()), self._gaps, self._top)
        return starts[i] if i < self._n else self.total_estimate() - self._bottom

    def end(self, i: int) -> int:
        avg = self._avg()
        _, ends = self._backend.cumulative(self._fill(avg), self._gaps, self._top)
        return ends[i]

    def total_estimate(self) -> int:
        """Estimated full content height; exact once every block is measured."""
        avg = self._avg()
        heights = self._fill(avg)
        _, ends = self._backend.cumulative(heights, self._gaps, self._top)
        return (ends[-1] if ends else self._top) + self._bottom

    def estimated_table(self) -> OffsetTable:
        """Snapshot :class:`OffsetTable` filling unknown heights with the running average — drives
        :meth:`OffsetTable.visible_range` for incremental scroll before every block is measured."""
        heights = self._fill(self._avg())
        starts, ends = self._backend.cumulative(heights, self._gaps, self._top)
        return OffsetTable(starts, ends, self._top, self._bottom)

    def exact_table(self) -> OffsetTable:
        """Exact :class:`OffsetTable`; requires every block measured."""
        if any(h is None for h in self._heights):
            raise ValueError("exact_table requires every block height to be set")
        return build_offsets(
            [h for h in self._heights if h is not None],
            self._gaps,
            self._top,
            self._bottom,
            self._backend,
        )
