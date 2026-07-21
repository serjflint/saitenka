"""Rasterize a single line of shaped text onto a transparent RGBA image.

Fallback-aware: the string is split into font runs (:mod:`overlay.fonts`) and each run is drawn on
a shared baseline, so mixed JP/EN renders without tofu.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from overlay import fonts

RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class TextOpts:
    size: int = 32
    weight: int = 400
    color: RGBA = (0, 0, 0, 255)
    padding: int = 8
    background: RGBA = (0, 0, 0, 0)  # transparent


def measure(text: str, opts: TextOpts) -> tuple[int, int, int]:
    """Return (width, height, baseline_y) for the shaped line, including padding."""
    width = 0.0
    for run in fonts.resolve_runs(text):
        font = fonts.load(fonts.FontSpec(run.file, opts.size, opts.weight))
        width += font.getlength(run.text)
    # Vertical metrics from the primary font at this size (consistent baseline for all runs).
    primary = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], opts.size, opts.weight))
    ascent, descent = primary.getmetrics()
    w = int(width + 2 * opts.padding + 0.5)
    h = ascent + descent + 2 * opts.padding
    baseline_y = opts.padding + ascent
    return w, h, baseline_y


def draw_line(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, opts: TextOpts) -> float:
    """Draw one shaped line at (x, baseline_y) using left-baseline anchor. Returns end x."""
    x0, baseline_y = xy
    x = float(x0)
    for run in fonts.resolve_runs(text):
        font = fonts.load(fonts.FontSpec(run.file, opts.size, opts.weight))
        draw.text((x, baseline_y), run.text, font=font, fill=opts.color, anchor="ls")
        x += font.getlength(run.text)
    return x


def rasterize(text: str, opts: TextOpts | None = None) -> Image.Image:
    """Render one line of text to a tightly-sized transparent RGBA image."""
    opts = opts or TextOpts()
    w, h, baseline_y = measure(text, opts)
    img = Image.new("RGBA", (w, h), opts.background)
    draw = ImageDraw.Draw(img)
    draw_line(draw, (opts.padding, baseline_y), text, opts)
    return img
