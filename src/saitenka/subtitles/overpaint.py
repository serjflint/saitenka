"""Device 2 of the colour ladder: the reading-state colour as a raster, not as text.

The text device (`overprint`) redraws each token through mpv's OSD libass, which cannot load a
family that only reached the *subtitle* renderer — a container attachment or an in-file ``[Fonts]``
section. Those are exactly the releases whose typesetting this mode exists to preserve, so standing
down there means no colour on the tracks that most want it.

This device needs no face at all, and it rasterises nothing new. **The glyphs here are libass's** —
the measuring render already drew every token from the same font set mpv's subtitle renderer holds,
and its anti-aliased coverage is kept (`TokenGeometry.coverage`). Tinting that mask *is* the raster.

That is what makes the device safe, and it is worth stating because the obvious reading is wrong:
drawing the text again with a *different* rasteriser — Pillow, say — would give it Pillow's shaping,
hinting and fallback, so the colour would sit on differently shaped letters than mpv's. That is the
same failure as device 1 on a substitute face, just with a different substitute. Nothing below
touches a font; the only image library in the chain is the one that carries the array to mpv.

What it cannot do is what device 1 gets for free: mpv scales an `osd-overlay` payload with its OSD
surface, and a bitmap uploaded through `overlay-add` is fixed to the pixels it was rasterised at. So
the caller must re-publish when the surface changes, which is already how a cue's geometry is keyed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

#: A cue larger than this is not a cue. The cap exists because the composite is allocated from
#: numbers that arrive from a render, and a corrupt extent must not become an allocation.
MAX_COMPOSITE_PIXELS = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TokenMask:
    """One token's coverage at its place in the frame, and the colour to tint it."""

    x: int
    y: int
    width: int
    height: int
    coverage: bytes
    rgb: int

    @property
    def usable(self) -> bool:
        return (
            self.width > 0
            and self.height > 0
            and len(self.coverage) == self.width * self.height
            and 0 <= self.rgb <= 0xFFFFFF
        )


@dataclass(frozen=True, slots=True)
class Overpaint:
    """An RGBA image and where in the frame it goes."""

    x: int
    y: int
    rgba: np.ndarray


def compose(masks: Sequence[TokenMask]) -> Overpaint | None:
    """Tint each mask and composite them into one image, or `None` when there is nothing to draw.

    Cropped to the union of the tokens rather than sized to the frame: a cue occupies a strip near
    one edge, and uploading a screen-sized buffer per cue would be most of a megabyte of zeroes.

    Tokens are painted with `maximum`, not addition. They do not overlap by construction — the
    measurement rejects an ambiguous overlap outright — but a shared anti-aliased edge pixel is
    normal, and adding there would print a bright seam between two neighbouring words.
    """
    usable = [mask for mask in masks if mask.usable]
    if not usable:
        return None
    left = min(mask.x for mask in usable)
    top = min(mask.y for mask in usable)
    right = max(mask.x + mask.width for mask in usable)
    bottom = max(mask.y + mask.height for mask in usable)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or width * height > MAX_COMPOSITE_PIXELS:
        return None
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    for mask in usable:
        alpha = np.frombuffer(mask.coverage, dtype=np.uint8).reshape(mask.height, mask.width)
        y0, x0 = mask.y - top, mask.x - left
        window = rgba[y0 : y0 + mask.height, x0 : x0 + mask.width]
        painted = alpha > window[..., 3]
        window[..., 0] = np.where(painted, (mask.rgb >> 16) & 0xFF, window[..., 0])
        window[..., 1] = np.where(painted, (mask.rgb >> 8) & 0xFF, window[..., 1])
        window[..., 2] = np.where(painted, mask.rgb & 0xFF, window[..., 2])
        window[..., 3] = np.maximum(window[..., 3], alpha)
    return Overpaint(left, top, rgba)
