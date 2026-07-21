"""Pure-Python compositor — simulate mpv drawing the panel over a video frame.

Used for a deterministic screenshot test: mpv composites the ``overlay-add`` BGRA bitmap over the
video in the same single surface, so alpha-blending the panel over a synthetic frame here reproduces
what the user sees (and, unlike a live mpv window, runs in CI). The live end-to-end check lives in
``examples/mpv_overlay.py``.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from overlay.model import Span, Style
from overlay.render.flow import render_flow
from overlay.render.layout import Block


def make_frame(width: int = 1280, height: int = 720, subtitle: str | None = None) -> Image.Image:
    """A synthetic 'video frame': a vertical gradient plus an optional centred subtitle line."""
    ys = np.linspace(0.0, 1.0, height)[:, None]
    r = np.repeat((30 + 40 * ys).astype(np.uint8), width, axis=1)
    g = np.repeat((45 + 60 * ys).astype(np.uint8), width, axis=1)
    b = np.repeat((70 + 90 * ys).astype(np.uint8), width, axis=1)
    a = np.full((height, width), 255, np.uint8)
    img = Image.fromarray(np.dstack([r, g, b, a]), "RGBA")
    if subtitle:
        line = render_flow(
            [Span(subtitle, Style(size=40, weight=700, color=(255, 255, 255, 255)))],
            Block(width=width - 200, padding=0, background=(0, 0, 0, 0)),
        )
        bx = (width - line.width) // 2
        by = height - line.height - 48
        shade = Image.new("RGBA", (line.width + 32, line.height + 16), (0, 0, 0, 120))
        img.alpha_composite(shade, (bx - 16, by - 8))
        img.alpha_composite(line, (bx, by))
    return img


def composite(frame: Image.Image, panel: Image.Image, x: int, y: int) -> Image.Image:
    """Alpha-blend ``panel`` onto a copy of ``frame`` at (x, y) — what mpv's OSD does."""
    out = frame.convert("RGBA").copy()
    out.alpha_composite(panel, (int(x), int(y)))
    return out
