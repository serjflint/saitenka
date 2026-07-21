"""Isolated ruby (base + furigana).

No Rust/Python lib gives ruby for free, so this is a custom pass over the shaped base + reading. The
reading is rendered ~half the base size, centred over the base; the box advance width is
``max(base_width, reading_width)`` and it reserves height above the baseline for the reading. This is
the geometry the flow renderer treats as an inline element inside wrapped rich text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.model import RichText, Span, Style
from overlay.render.layout import draw_inline, inline_width

READING_SCALE = 0.5
RUBY_GAP = 1  # px between furigana descent and base cap


@dataclass
class RubyBox:
    """A base run with furigana centred above it, positioned relative to the main baseline."""

    base: RichText
    reading: str
    reading_style: Style
    base_width: float
    reading_width: float
    box_width: float
    ascent: int  # extent above the main baseline (base cap + gap + full reading)
    descent: int  # extent below the main baseline (base descent)
    base_ascent: int
    reading_baseline_dy: int = field(
        default=0
    )  # reading baseline offset above main baseline (>0 up)

    @property
    def advance(self) -> float:
        return self.box_width

    def base_x(self, x: float) -> float:
        return x + (self.box_width - self.base_width) / 2

    def reading_x(self, x: float) -> float:
        return x + (self.box_width - self.reading_width) / 2

    def draw(self, img: Image.Image, draw: ImageDraw.ImageDraw, x: float, baseline: float) -> None:
        draw_inline(img, draw, self.base_x(x), baseline, self.base)
        rb = baseline - self.reading_baseline_dy
        draw_inline(img, draw, self.reading_x(x), rb, [Span(self.reading, self.reading_style)])


def _base_size(base: RichText) -> int:
    return base[0].style.size if base else Style().size


def layout_ruby(base: RichText, reading: str, reading_scale: float = READING_SCALE) -> RubyBox:
    """Compute ruby geometry: reading centred over base, height reserved above the baseline."""
    base_size = _base_size(base)
    base_color = base[0].style.color if base else Style().color
    r_style = Style(size=max(1, round(base_size * reading_scale)), weight=400, color=base_color)

    base_width = inline_width(base)
    reading_width = inline_width([Span(reading, r_style)])
    box_width = max(base_width, reading_width)

    base_font = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], base_size))
    base_ascent, base_descent = base_font.getmetrics()
    r_font = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], r_style.size))
    r_ascent, r_descent = r_font.getmetrics()

    # reading sits just above the base cap; its baseline is that far above the main baseline
    reading_baseline_dy = base_ascent + RUBY_GAP + r_descent
    ascent = reading_baseline_dy + r_ascent
    return RubyBox(
        base=base,
        reading=reading,
        reading_style=r_style,
        base_width=base_width,
        reading_width=reading_width,
        box_width=box_width,
        ascent=ascent,
        descent=base_descent,
        base_ascent=base_ascent,
        reading_baseline_dy=reading_baseline_dy,
    )


def render_ruby(
    base: RichText,
    reading: str,
    padding: int = 8,
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> Image.Image:
    """Render a single isolated ruby box to a tight transparent image (for goldens/eyeballing)."""
    box = layout_ruby(base, reading)
    w = int(box.box_width + 2 * padding + 0.5)
    h = box.ascent + box.descent + 2 * padding
    img = Image.new("RGBA", (w, h), background)
    draw = ImageDraw.Draw(img)
    baseline = padding + box.ascent
    box.draw(img, draw, padding, baseline)
    return img
