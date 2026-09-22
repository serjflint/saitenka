"""Whole-cue raster composition values and resource bound."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# A corrupt rendered extent must not become an unbounded allocation.
MAX_COMPOSITE_PIXELS = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Overpaint:
    """An RGBA image and where in the frame it goes."""

    x: int
    y: int
    rgba: np.ndarray
