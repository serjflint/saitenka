"""A small transient status overlay for mining feedback (mined / duplicate / error)."""

from __future__ import annotations

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.model import RGBA, Span, Style
from overlay.render.layout import draw_inline, inline_width

BG_OK: RGBA = (34, 46, 38, 235)
BG_WARN: RGBA = (58, 48, 30, 235)
BG_ERR: RGBA = (58, 32, 34, 235)
FG: RGBA = (230, 234, 228, 255)
ACCENT = {"ok": (166, 218, 149, 255), "warn": (238, 212, 130, 255), "err": (237, 135, 150, 255)}


def render_toast(text: str, kind: str = "ok", size: int = 30) -> Image.Image:
    bg = {"ok": BG_OK, "warn": BG_WARN, "err": BG_ERR}.get(kind, BG_OK)
    icon = {"ok": "✚", "warn": "●", "err": "×"}.get(kind, "●")
    pad_x, pad_y = 22, 14
    spans = [
        Span(icon + "  ", Style(size=size, color=ACCENT.get(kind, FG))),
        Span(text, Style(size=size, weight=500, color=FG)),
    ]
    w = int(inline_width(spans)) + 2 * pad_x
    ascent, descent = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], size)).getmetrics()
    h = ascent + descent + 2 * pad_y
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=12, fill=bg, outline=ACCENT.get(kind, FG), width=2
    )
    draw_inline(img, draw, pad_x, pad_y + ascent, spans)
    return img
