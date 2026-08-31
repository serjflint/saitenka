"""Chip / pill / bordered-label sprite.

The fanciest-looking but simplest element: a rounded rectangle with centred rich text. Used for
frequency pills, dictionary-name pills, and — with a transparent fill + border — bordered labels
like 逆引き.

Split in two, along the scale boundary: ``*_metrics`` computes the box in REFERENCE px (what layout
reserves, scale-free), ``_draw_*`` projects that box by the display scale and re-runs the font at
``size×scale``. There is deliberately no ``scale == 1.0`` branch — 1.0 projects to itself, so the
reference raster is the same code path as every other, and the two cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from saitenka import fonts
from saitenka.model import RGBA, Span, Style
from saitenka.render.layout import draw_inline, inline_width


@dataclass(frozen=True)
class ChipStyle:
    size: int = 20
    weight: int = 500
    fg: RGBA = (255, 255, 255, 255)
    bg: RGBA = (90, 122, 160, 255)
    border: RGBA | None = None
    border_w: int = 1
    pad_h: int | None = None
    pad_v: int | None = None
    radius: int | None = None
    # two-tone pill (SubMiner-style frequency chip): colored name segment + attached value segment
    value: str | None = None
    value_bg: RGBA = (245, 245, 245, 255)
    value_fg: RGBA = (40, 40, 40, 255)


@dataclass
class Sprite:
    image: Image.Image
    baseline: int  # y of the text baseline within the sprite

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def _dev(v: float, scale: float, floor: int = 0) -> int:
    """A reference-px metric projected to device px. Identity at 1.0 — ints round to themselves."""
    return max(floor, round(v * scale))


def _text_box(text: str, cs: ChipStyle, pad_v: float) -> tuple[float, float]:
    """(height, baseline) from the primary font's glyph bbox — a tight vertical extent, not the em."""
    primary = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], cs.size, cs.weight))
    _l, t, _r, b = primary.getbbox(text, anchor="ls")
    return (b - t) + 2 * pad_v, -t + pad_v


def _plain_metrics(text: str, cs: ChipStyle) -> tuple[int, float, float, int]:
    """(width, height, baseline, radius) in REFERENCE px — the box layout reserves for a plain pill."""
    pad_h = cs.pad_h if cs.pad_h is not None else max(4, round(cs.size * 0.45))
    pad_v = cs.pad_v if cs.pad_v is not None else max(2, round(cs.size * 0.18))
    radius = cs.radius if cs.radius is not None else max(3, round(cs.size * 0.35))
    width = round(inline_width([Span(text, Style(size=cs.size, weight=cs.weight))])) + 2 * pad_h
    height, baseline = _text_box(text or "M", cs, pad_v)
    return width, height, baseline, radius


def _two_tone_metrics(name: str, cs: ChipStyle) -> tuple[int, float, float, int, int]:
    """As :func:`_plain_metrics`, plus the colored name segment's width (the divider's x)."""
    pad_h = cs.pad_h if cs.pad_h is not None else max(5, round(cs.size * 0.5))
    pad_v = cs.pad_v if cs.pad_v is not None else max(3, round(cs.size * 0.28))
    radius = cs.radius if cs.radius is not None else max(4, round(cs.size * 0.42))
    style = Style(size=cs.size, weight=cs.weight)
    name_seg = round(inline_width([Span(name, style)])) + 2 * pad_h
    total = name_seg + round(inline_width([Span(cs.value or "", style)])) + 2 * pad_h
    height, baseline = _text_box("あ", cs, pad_v)
    return total, height, baseline, radius, name_seg


def _draw_centred(
    img: Image.Image, draw: ImageDraw.ImageDraw, x0: int, x1: int, baseline: int, spans: list[Span]
) -> None:
    """Centre ``spans`` in ``[x0, x1)``. The text is sized independently of the box (an integer font
    size is not linear in the scale), so the slack is split between the two pads rather than piling
    onto the right one."""
    draw_inline(img, draw, x0 + (x1 - x0 - inline_width(spans)) / 2, baseline, spans)


def _draw_plain(text: str, cs: ChipStyle, scale: float) -> Sprite:
    pill_w, pill_h, baseline, radius = _plain_metrics(text, cs)
    w, h = _dev(pill_w, scale, 1), _dev(pill_h, scale, 1)
    r, bl = _dev(radius, scale), _dev(baseline, scale)
    style = Style(size=_dev(cs.size, scale, 1), weight=cs.weight, color=cs.fg)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if cs.bg[3] > 0 or cs.border is not None:
        draw.rounded_rectangle(
            [0, 0, w - 1, h - 1],
            radius=r,
            fill=cs.bg,
            outline=cs.border,
            width=_dev(cs.border_w if cs.border else 1, scale, 1),
        )
    _draw_centred(img, draw, 0, w, bl, [Span(text, style)])
    return Sprite(img, bl)


def _draw_two_tone(name: str, cs: ChipStyle, scale: float) -> Sprite:
    total_w, pill_h, baseline, radius, name_seg_w = _two_tone_metrics(name, cs)
    w, h = _dev(total_w, scale, 1), _dev(pill_h, scale, 1)
    seg, r, bl = _dev(name_seg_w, scale, 1), _dev(radius, scale), _dev(baseline, scale)
    size = _dev(cs.size, scale, 1)
    name_style = Style(size=size, weight=cs.weight, color=cs.fg)
    val_style = Style(size=size, weight=cs.weight, color=cs.value_fg)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # whole pill: light value fill + a colored border (the value segment reads as bordered)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=r, fill=cs.value_bg, outline=cs.bg, width=_dev(1, scale, 1)
    )
    # left segment: colored, rounded on the left only → clean vertical divider
    draw.rounded_rectangle(
        [0, 0, seg - 1, h - 1], radius=r, fill=cs.bg, corners=(True, False, False, True)
    )
    _draw_centred(img, draw, 0, seg, bl, [Span(name, name_style)])
    _draw_centred(img, draw, seg, w, bl, [Span(cs.value or "", val_style)])
    return Sprite(img, bl)


def render_chip(text: str, cs: ChipStyle, *, scale: float = 1.0) -> Sprite:
    """The pill rastered at ``scale``: glyphs at ``size×scale``, pads/radius/border projected, drawn
    into the reference box projected by the same factor — never its own natural extent, which drifts
    off the slot layout reserved and can close the gap to the neighbouring pill.

    The returned sprite's metrics are therefore DEVICE px; layout measures the 1× sprite instead (see
    :class:`~saitenka.render.flow.ChipBox`)."""
    if cs.value is not None:
        return _draw_two_tone(text, cs, scale)
    return _draw_plain(text, cs, scale)
