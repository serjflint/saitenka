"""Recover redraw positions from native coverage, without integer ink-box subtraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class PhaseMask:
    width: int
    height: int
    coverage: bytes
    dx: float
    dy: float


def match_phases(
    coverage: bytes, width: int, height: int, atlases: Iterable[Sequence[PhaseMask]]
) -> tuple[tuple[float, float], ...] | None:
    """Exact left-to-right, disjoint glyph decomposition; refuse incomplete coverage.

    Each atlas contains isolated rasters and their ink-to-anchor offsets. Matching includes every
    alpha byte, including detached marks. Overlapping or reordered glyphs belong on the mask device.
    """
    if width <= 0 or height <= 0 or len(coverage) != width * height:
        return None
    remaining = np.frombuffer(coverage, dtype=np.uint8).reshape(height, width).copy()
    positions: list[tuple[float, float]] = []
    for atlas in atlases:
        columns = np.flatnonzero(np.any(remaining, axis=0))
        if not len(columns):
            return None
        left = int(columns[0])
        match = _match_glyph(remaining, left, atlas)
        if match is None:
            return None
        phase, top = match
        remaining[top : top + phase.height, left : left + phase.width] = 0
        positions.append((left - phase.dx, top - phase.dy))
    return tuple(positions) if not remaining.any() else None


def _match_glyph(
    remaining: np.ndarray, left: int, atlas: Sequence[PhaseMask]
) -> tuple[PhaseMask, int] | None:
    for phase in atlas:
        if (
            phase.width <= 0
            or phase.height <= 0
            or len(phase.coverage) != phase.width * phase.height
            or left + phase.width > remaining.shape[1]
            or phase.height > remaining.shape[0]
        ):
            continue
        mask = np.frombuffer(phase.coverage, dtype=np.uint8).reshape(phase.height, phase.width)
        edge = np.flatnonzero(mask[:, 0])
        if not len(edge):
            continue
        first = int(edge[0])
        for row in np.flatnonzero(remaining[:, left] == mask[first, 0]):
            top = int(row) - first
            if top < 0 or top + phase.height > remaining.shape[0]:
                continue
            if np.array_equal(remaining[top : top + phase.height, left : left + phase.width], mask):
                return phase, top
    return None
