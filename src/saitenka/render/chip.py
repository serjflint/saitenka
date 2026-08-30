"""Chip / pill / bordered-label sprite.

The fanciest-looking but simplest element: a rounded rectangle with centred rich text. Used for
frequency pills, dictionary-name pills, and — with a transparent fill + border — bordered labels
like 逆引き.

A pill is drawn TWICE over its life: once at 1× for the metrics layout reserves, and again at the
display scale for the pixels that ship. The second pass re-runs the font at ``size×scale`` rather
than stretching the first, so a pill is as crisp as the text beside it — but the box it fills is the
1× box projected, never its own natural extent. That split is what keeps the two passes agreeing:
an integer font size does not scale linearly, so a freely-sized native pill drifts several px wide of
its slot and closes the gap to its neighbour.
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
    return max(floor, round(v * scale))


def _render_two_tone(name: str, cs: ChipStyle, scale: float) -> Sprite:
    """A connected two-segment pill: colored name segment + light value segment (frequency pills)."""
    pad_h = cs.pad_h if cs.pad_h is not None else max(5, round(cs.size * 0.5))
    pad_v = cs.pad_v if cs.pad_v is not None else max(3, round(cs.size * 0.28))
    radius = cs.radius if cs.radius is not None else max(4, round(cs.size * 0.42))
    name_style = Style(size=cs.size, weight=cs.weight, color=cs.fg)
    val_style = Style(size=cs.size, weight=cs.weight, color=cs.value_fg)
    name_w = inline_width([Span(name, name_style)])
    val_w = inline_width([Span(cs.value or "", val_style)])

    primary = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], cs.size, cs.weight))
    _, t, _, b = primary.getbbox("あ", anchor="ls")
    pill_h = (b - t) + 2 * pad_v
    baseline = -t + pad_v
    name_seg_w = round(name_w) + 2 * pad_h
    total_w = name_seg_w + round(val_w) + 2 * pad_h
    if scale != 1.0:
        return _native_two_tone(name, cs, scale, (total_w, pill_h, baseline, radius, name_seg_w))

    img = Image.new("RGBA", (total_w, pill_h), (0, 0, 0, 0))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # float h: int() would shift golden geometry
    draw = ImageDraw.Draw(img)
    # whole pill: light value fill + a colored border (the value segment reads as bordered)
    draw.rounded_rectangle(
        [0, 0, total_w - 1, pill_h - 1], radius=radius, fill=cs.value_bg, outline=cs.bg, width=1
    )
    # left segment: colored, rounded on the left only → clean vertical divider
    draw.rounded_rectangle(
        [0, 0, name_seg_w - 1, pill_h - 1],
        radius=radius,
        fill=cs.bg,
        corners=(True, False, False, True),
    )
    draw_inline(img, draw, pad_h, baseline, [Span(name, name_style)])
    draw_inline(img, draw, name_seg_w + pad_h, baseline, [Span(cs.value or "", val_style)])
    return Sprite(img, baseline)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # float baseline is drawn as-is


def _native_two_tone(
    name: str, cs: ChipStyle, scale: float, ref: tuple[int, float, float, int, int]
) -> Sprite:
    """The two-tone pill redrawn in device px inside ``ref`` (its 1× box) projected by ``scale``."""
    total_w, pill_h, baseline, radius, name_seg_w = ref
    w, h = _dev(total_w, scale, 1), _dev(pill_h, scale, 1)
    seg, r, bl = _dev(name_seg_w, scale, 1), _dev(radius, scale), _dev(baseline, scale)
    size = _dev(cs.size, scale, 1)
    name_style = Style(size=size, weight=cs.weight, color=cs.fg)
    val_style = Style(size=size, weight=cs.weight, color=cs.value_fg)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=r, fill=cs.value_bg, outline=cs.bg, width=_dev(1, scale, 1)
    )
    draw.rounded_rectangle(
        [0, 0, seg - 1, h - 1], radius=r, fill=cs.bg, corners=(True, False, False, True)
    )
    _draw_centred(img, draw, 0, seg, bl, [Span(name, name_style)])
    _draw_centred(img, draw, seg, w, bl, [Span(cs.value or "", val_style)])
    return Sprite(img, bl)


def _draw_centred(
    img: Image.Image, draw: ImageDraw.ImageDraw, x0: int, x1: int, baseline: int, spans: list[Span]
) -> None:
    """Centre ``spans`` in ``[x0, x1)``. The native text is a few px off ``scale`` × the 1× text, so
    the slack is split between the two pads instead of piling up on the right one."""
    draw_inline(img, draw, x0 + (x1 - x0 - inline_width(spans)) / 2, baseline, spans)


def render_chip(text: str, cs: ChipStyle, *, scale: float = 1.0) -> Sprite:
    """``scale`` > 1 rasters the pill natively at ``size×scale`` into its projected 1× box (see the
    module docstring). The sprite's own metrics are then DEVICE px, so layout must keep measuring the
    1× sprite — :class:`~saitenka.render.flow.ChipBox` owns that split."""
    if cs.value is not None:
        return _render_two_tone(text, cs, scale)
    pad_h = cs.pad_h if cs.pad_h is not None else max(4, round(cs.size * 0.45))
    pad_v = cs.pad_v if cs.pad_v is not None else max(2, round(cs.size * 0.18))
    radius = cs.radius if cs.radius is not None else max(3, round(cs.size * 0.35))

    style = Style(size=cs.size, weight=cs.weight, color=cs.fg)
    text_w = inline_width([Span(text, style)])

    # Tight vertical extent from the primary font's glyph bbox at the baseline.
    primary = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], cs.size, cs.weight))
    _l, t, _r, b = primary.getbbox(text or "M", anchor="ls")
    top, bottom = t, b
    pill_h = (bottom - top) + 2 * pad_v
    baseline = -top + pad_v
    pill_w = round(text_w) + 2 * pad_h
    if scale != 1.0:
        return _native_plain(text, cs, scale, (pill_w, pill_h, baseline, radius))

    img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # float h: see above
    draw = ImageDraw.Draw(img)
    if cs.bg[3] > 0 or cs.border is not None:
        draw.rounded_rectangle(
            [0, 0, pill_w - 1, pill_h - 1],
            radius=radius,
            fill=cs.bg,
            outline=cs.border,
            width=cs.border_w if cs.border else 1,
        )
    draw_inline(img, draw, pad_h, baseline, [Span(text, style)])
    return Sprite(img, baseline)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # float baseline is drawn as-is


def _native_plain(
    text: str, cs: ChipStyle, scale: float, ref: tuple[int, float, float, int]
) -> Sprite:
    """The plain pill redrawn in device px inside ``ref`` (its 1× box) projected by ``scale``."""
    pill_w, pill_h, baseline, radius = ref
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
