"""Pluggable block-geometry backend for the windowed renderer (#113).

Row-stack geometry — each row's top/bottom in content space from its height, the inter-row gaps, and the
top/bottom margins — was hand-rolled arithmetic (``window._cumulative``). This lifts it behind a
:class:`LayoutBackend` seam, mirroring the raster-backend seam: a backend's :meth:`~LayoutBackend.solve`
takes the measured heights + gaps + padding and returns the placed rects (with a deferred-measure hook,
so an off-screen tall row is measured but never laid out beyond its extent).

- :class:`DefaultLayoutBackend` — the incremental cumulative sum, the behaviour-identical default that
  every golden is pinned to.
- :class:`FlexColumnBackend` — an independent ``flex-direction: column; justify-content: flex-start``
  solver (main-size = row height, ``gap`` between items, ``padding-block`` = margins). Opt-in and
  parity-gated: a differential test proves it agrees with the default on random and real panels
  (``tests/test_layout_backend.py``), and it satisfies the vendored column-layout fixtures.

The public API is ``solve(rows, width, measure) -> LayoutResult`` (rects + paint order); the
``cumulative`` primitive it is built on is what ``window`` consumes so the two representations never drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import accumulate
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@dataclass(frozen=True, slots=True)
class Rect:
    """A placed row: content-space top-left ``(x, y)`` and size ``(w, h)``."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class LayoutResult:
    """The solved column: per-row ``rects`` in paint ``order`` (top-down), plus the primitive
    ``starts``/``ends`` (row tops/bottoms) and the ``total`` content height incl. both margins."""

    starts: tuple[int, ...]
    ends: tuple[int, ...]
    rects: tuple[Rect, ...]
    order: tuple[int, ...]
    total: int


@runtime_checkable
class LayoutBackend(Protocol):
    """Computes row-stack geometry. ``cumulative`` is the primitive the offset table is grown from;
    ``solve`` is the full placement (rects + order) mirroring the raster-backend seam."""

    def cumulative(
        self, heights: Sequence[int], gaps: Sequence[int], top_pad: int
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """``(starts, ends)`` — ``start_i = top_pad + Σ_{j<i}(h_j + gap_j)``, ``end_i = start_i + h_i``.
        The gap after the last row is never added (``gaps[n-1]`` ignored)."""
        ...

    def solve(
        self,
        rows: Sequence[int],
        width: int,
        measure: Callable[[int], int] | None = None,
        *,
        gaps: Sequence[int],
        top_pad: int,
        bottom_pad: int,
        x: Sequence[int] | int = 0,
    ) -> LayoutResult:
        """Place ``rows`` (row indices; ``measure(i)`` yields row ``i``'s height, or ``rows`` already
        holds heights when ``measure`` is None) into a ``width``-wide column. ``x`` is each row's left
        (a scalar applies to all). Deferred: ``measure`` is called lazily, once per row."""
        ...


def _cumulative(
    heights: Sequence[int], gaps: Sequence[int], top_pad: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Canonical cumulative offsets (the old ``window._cumulative``, now the default backend's core)."""
    n = len(heights)
    starts: list[int] = []
    ends: list[int] = []
    y = top_pad
    for i, h in enumerate(heights):
        starts.append(y)
        ends.append(y + h)
        y += h + (gaps[i] if i < n - 1 else 0)
    return tuple(starts), tuple(ends)


def _solve(
    backend: LayoutBackend,
    rows: Sequence[int],
    width: int,
    measure: Callable[[int], int] | None,
    gaps: Sequence[int],
    top_pad: int,
    bottom_pad: int,
    x: Sequence[int] | int,
) -> LayoutResult:
    """Shared ``solve`` body: resolve heights (via ``measure`` or ``rows`` directly), run the backend's
    ``cumulative``, and assemble the rects + top-down paint order. Same output for every backend."""
    heights = [measure(i) for i in range(len(rows))] if measure is not None else list(rows)
    starts, ends = backend.cumulative(heights, gaps, top_pad)
    xs = [x] * len(heights) if isinstance(x, int) else list(x)
    rects = tuple(Rect(xs[i], starts[i], width, heights[i]) for i in range(len(heights)))
    total = (ends[-1] if ends else top_pad) + bottom_pad
    return LayoutResult(starts, ends, rects, tuple(range(len(heights))), total)


class DefaultLayoutBackend:
    """The behaviour-identical default: the incremental cumulative sum every golden is pinned to."""

    def cumulative(
        self, heights: Sequence[int], gaps: Sequence[int], top_pad: int
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        return _cumulative(heights, gaps, top_pad)

    def solve(
        self,
        rows: Sequence[int],
        width: int,
        measure: Callable[[int], int] | None = None,
        *,
        gaps: Sequence[int],
        top_pad: int,
        bottom_pad: int,
        x: Sequence[int] | int = 0,
    ) -> LayoutResult:
        return _solve(self, rows, width, measure, gaps, top_pad, bottom_pad, x)


class FlexColumnBackend:
    """Independent ``flex-direction: column; justify-content: flex-start`` solver (no grow/shrink — the
    tooltip's rows are fixed main-size). Placement is a prefix-sum over the ``[h0, gap0, h1, gap1, …]``
    interleaved main-axis track, a different computation than the default's running accumulator, so the
    differential parity test is a real cross-check rather than the same code twice."""

    def cumulative(
        self, heights: Sequence[int], gaps: Sequence[int], top_pad: int
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        n = len(heights)
        if n == 0:
            return (), ()
        track: list[int] = []
        for i, h in enumerate(heights):
            track.append(h)
            if i < n - 1:
                track.append(gaps[i])
        prefix = list(accumulate(track, initial=top_pad))  # main-axis edge before each track cell
        starts = tuple(prefix[2 * i] for i in range(n))  # even cells are rows; odd cells are gaps
        ends = tuple(starts[i] + heights[i] for i in range(n))
        return starts, ends

    def solve(
        self,
        rows: Sequence[int],
        width: int,
        measure: Callable[[int], int] | None = None,
        *,
        gaps: Sequence[int],
        top_pad: int,
        bottom_pad: int,
        x: Sequence[int] | int = 0,
    ) -> LayoutResult:
        return _solve(self, rows, width, measure, gaps, top_pad, bottom_pad, x)


class TaffyLayoutBackend:
    """Row-stack geometry via taffy's flexbox solver — the optional ``taffylite`` Rust engine (#146).

    A ``flex-direction: column`` of fixed-height rows with per-row ``margin-bottom`` gaps inside
    ``top_pad`` top padding, which taffy places identically to :func:`_cumulative` (integer-exact for
    integer inputs). Opt-in and parity-gated: the differential test proves it agrees byte-for-byte with
    :class:`DefaultLayoutBackend` on random and real panels, and it satisfies the vendored column
    fixtures — so it is a true drop-in, chosen for a mature CSS engine's robustness, not speed (both are
    µs-scale, dominated by Pillow raster).

    ``taffylite`` is imported lazily HERE and only here — the single chokepoint mirroring the GPL
    ``saitenka_deinflect`` add-on, enforced by the ``.importlinter`` ``layout-engine-chokepoint``
    forbidden contract and the ruff ``TID251`` banned-api (per-file-ignored only for this module); deptry
    stays green with no ignore, since taffylite is a declared optional dep. The lazy import keeps the
    pure-Python default from ever pulling the Rust extension; the wheel is an opt-in extra
    (``saitenka[layout-engine]``)."""

    def cumulative(
        self, heights: Sequence[int], gaps: Sequence[int], top_pad: int
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        import taffylite  # noqa: TID251  # layout-engine chokepoint: this module is the sole sanctioned importer (see class docstring)

        starts, ends = taffylite.column(
            [float(h) for h in heights], [float(g) for g in gaps], float(top_pad)
        )
        return tuple(starts), tuple(ends)

    def solve(
        self,
        rows: Sequence[int],
        width: int,
        measure: Callable[[int], int] | None = None,
        *,
        gaps: Sequence[int],
        top_pad: int,
        bottom_pad: int,
        x: Sequence[int] | int = 0,
    ) -> LayoutResult:
        return _solve(self, rows, width, measure, gaps, top_pad, bottom_pad, x)


DEFAULT_BACKEND: LayoutBackend = DefaultLayoutBackend()


def backend_label(backend: LayoutBackend) -> str:
    """Short tag for a backend instance ('taffy' | 'flex' | 'default') — the EFFECTIVE engine, for log
    lines and span attributes (so a taffy request that fell back reads 'default', the truth)."""
    return {"TaffyLayoutBackend": "taffy", "FlexColumnBackend": "flex"}.get(
        type(backend).__name__, "default"
    )


def resolve_backend(engine: str) -> LayoutBackend:
    """Map a ``[tooltip] layout_engine`` name to a backend instance. ``"taffy"`` needs the optional
    ``taffylite`` wheel (``saitenka[layout-engine]``) — probed HERE, the sole chokepoint, so a missing
    wheel degrades to :data:`DEFAULT_BACKEND` (logged) instead of failing the reader. Any other value
    (incl. ``"default"``) is the pure-Python default."""
    if engine == "taffy":
        try:
            import taffylite  # noqa: F401,TID251  # availability probe; the real use is in TaffyLayoutBackend
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "layout_engine='taffy' but the taffylite wheel is not installed "
                "(pip install 'saitenka[layout-engine]'); using the default layout backend"
            )
            return DEFAULT_BACKEND
        return TaffyLayoutBackend()
    return DEFAULT_BACKEND
