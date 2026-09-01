"""Pillow renderer for the static episode-analysis overlay.

Draws label/value rows; it does not know what the numbers mean. Turning an ``EpisodeAnalysis`` into
rows is the analysis feature's job (``features/analysis/analysis_rows.py``) — this module used to
take the analysis object itself, which made a renderer unreadable without the application it draws
for, purely through a type annotation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from saitenka import fonts

if TYPE_CHECKING:
    from collections.abc import Sequence

BG = (13, 18, 26, 248)
ROW_BG = (25, 33, 45, 235)
WHITE = (244, 247, 251, 255)
MUTED = (157, 171, 190, 255)
ACCENT = (113, 190, 255, 255)


def _font(size: int, weight: int = 400):
    return fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], size, weight))


def render_analysis(
    rows: Sequence[tuple[str, str]],
    *,
    osd: tuple[int, int],
    close_key: str,
    scale: float = 1.0,
) -> Image.Image:
    def px(value: int) -> int:
        return max(1, round(value * scale))

    width = max(px(320), min(px(680), osd[0] - px(32)))
    height = px(78) + len(rows) * px(40) + px(38)
    image = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(image)
    title = _font(px(22), 650)
    body = _font(px(15))
    small = _font(px(12))
    draw.text((px(18), px(28)), "Episode analysis", font=title, fill=WHITE, anchor="lm")
    draw.text(
        (width - px(18), px(28)),
        f"{close_key} close",
        font=small,
        fill=MUTED,
        anchor="rm",
    )
    y = px(58)
    for label, value in rows:
        draw.rounded_rectangle((px(12), y, width - px(12), y + px(34)), radius=px(6), fill=ROW_BG)
        draw.text((px(22), y + px(17)), label, font=body, fill=ACCENT, anchor="lm")
        if value:
            draw.text((width - px(22), y + px(17)), value, font=body, fill=WHITE, anchor="rm")
        y += px(40)
    draw.text(
        (width // 2, height - px(18)),
        "Static subtitle-track metrics · playback unchanged",
        font=small,
        fill=MUTED,
        anchor="mm",
    )
    return image
