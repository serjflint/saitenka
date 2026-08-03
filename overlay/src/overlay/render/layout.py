"""Line wrapping + block layout, with per-span styling.

Japanese wraps between (almost) any two characters — there are no spaces — while Latin words stay
whole and break at spaces. So we tokenize into atomic units (one CJK char each; Latin/number runs as
whole words; whitespace as collapsible break points), greedily fill lines to a panel width, and apply
minimal *kinsoku* (line-break) rules so a line never *starts* with closing punctuation or a small
kana, and never *ends* with an opening bracket. Each token carries its own :class:`Style`, so one
wrapped paragraph can mix bold / colour / size. Line height adapts to the tallest token on each line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.model import RichText, Span, Style

if TYPE_CHECKING:
    from collections.abc import Sequence

# Characters that may not start a line (行頭禁則): closing punctuation, small kana, prolonged marks.
NO_START = set(
    "、。，．・！？：；）］｝〕〉》」』】〙〗〟!?),.:;]}"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ゛゜ーゝゞヽヾ々〜～"
)
# Characters that may not end a line (行末禁則): opening brackets.
NO_END = set("（［｛〔〈《「『【〘〖〝([{")

ITALIC_SHEAR = 0.22


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3000 <= o <= 0x303F  # CJK punctuation
        or 0x3040 <= o <= 0x30FF  # hiragana + katakana
        or 0x3400 <= o <= 0x4DBF  # CJK ext A
        or 0x4E00 <= o <= 0x9FFF  # CJK unified
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility ideographs
        or 0xFF00 <= o <= 0xFFEF  # fullwidth / halfwidth forms
    )


@dataclass
class Token:
    text: str
    file: str
    kind: str  # 'word' | 'cjk' | 'space'
    width: float
    style: Style
    href: str | None = None  # internal dict link target term, inherited from its Span


def _font(file: str, style: Style):
    return fonts.load(fonts.FontSpec(file, style.size, style.weight))


def _tokenize_span(text: str, style: Style, href: str | None = None) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            tokens.append(Token("\n", fonts.FONT_FILES[0], "space", 0.0, style, href))
            i += 1
        elif ch.isspace():
            j = i
            while j < n and text[j].isspace() and text[j] != "\n":
                j += 1
            seg = text[i:j]
            f = fonts.FONT_FILES[0]
            tokens.append(
                Token(seg, f, "space", fonts.text_width(_font(f, style), seg), style, href)
            )
            i = j
        elif _is_cjk(ch):
            f = fonts.font_for_char(ch)
            tokens.append(Token(ch, f, "cjk", fonts.text_width(_font(f, style), ch), style, href))
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and not _is_cjk(text[j]):
                j += 1
            seg = text[i:j]
            f = fonts.font_for_char(seg[0])
            tokens.append(
                Token(seg, f, "word", fonts.text_width(_font(f, style), seg), style, href)
            )
            i = j
    return tokens


def tokenize_rich(rich: RichText) -> list[Token]:
    """Break styled spans into atomic layout tokens with measured widths and resolved fonts."""
    tokens: list[Token] = []
    for span in rich:
        tokens.extend(_tokenize_span(span.text, span.style))
    return tokens


def wrap(tokens: list[Token], max_width: float) -> list[list[Token]]:
    """Pack tokens into lines ≤ max_width via the pure UAX #14 breaker (:func:`wrap_units`), honouring
    hard breaks (``\\n``) and kinsoku. The single-style counterpart to :func:`overlay.render.flow.wrap_items`."""
    from overlay.render.linebreak import wrap_units

    widths = [t.width for t in tokens]
    texts = [t.text for t in tokens]
    idx_lines = wrap_units(widths, texts, max_width, force_break=lambda k: tokens[k].text == "\n")
    return [[tokens[k] for k in line] for line in idx_lines]


def line_metrics(line: list[Token]) -> tuple[int, int]:
    """(ascent, descent) for a line = max over its tokens' fonts (so nothing clips)."""
    ascent = descent = 0
    for tok in line or []:
        a, d = _font(tok.file, tok.style).getmetrics()
        ascent, descent = max(ascent, a), max(descent, d)
    if ascent == 0:  # empty line: fall back to default style metrics
        a, d = _font(fonts.FONT_FILES[0], Style()).getmetrics()
        ascent, descent = a, d
    return ascent, descent


def draw_token(
    base: Image.Image, draw: ImageDraw.ImageDraw, x: float, baseline: float, tok: Token
) -> None:
    """Draw one token at pen (x, baseline). Faux-italic via shear when style.italic."""
    font = _font(tok.file, tok.style)
    if tok.style.underline and tok.kind != "space":
        uy = round(baseline + max(1, tok.style.size * 0.08))
        draw.line(
            [(x, uy), (x + tok.width, uy)], fill=tok.style.color, width=max(1, tok.style.size // 18)
        )
    if not tok.style.italic:
        # Split draw.text's upright path: cache the getmask2 alpha (the FreeType cost) via
        # fonts.glyph_mask, then blit through PIL's own draw_bitmap — a verbatim replay of
        # ImageDraw.text (int pen part + subpixel start baked into the mask), so it stays byte-identical
        # (and keeps draw_bitmap's edge clipping, which windowed bands rely on).
        ink, fill_ink = draw._getink(tok.style.color)
        if ink is None:
            ink = fill_ink
        core, (ox, oy) = fonts.glyph_mask(
            font, tok.text, draw.fontmode, (math.modf(x)[0], math.modf(baseline)[0])
        )
        draw.draw.draw_bitmap((int(x) + ox, int(baseline) + oy), core, ink)
        return
    a, d = font.getmetrics()
    pad = int(a * ITALIC_SHEAR) + 2
    stamp = Image.new("RGBA", (int(tok.width) + pad + 2, a + d), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((0, a), tok.text, font=font, fill=tok.style.color, anchor="ls")
    stamp = stamp.transform(
        stamp.size,
        Image.Transform.AFFINE,
        (1, ITALIC_SHEAR, -ITALIC_SHEAR * a, 0, 1, 0),
        resample=Image.Resampling.BILINEAR,
    )
    base.alpha_composite(stamp, (int(x), int(baseline - a)))


def inline_width(rich: RichText) -> float:
    return sum(t.width for t in tokenize_rich(rich))


def draw_inline(
    base: Image.Image, draw: ImageDraw.ImageDraw, x: float, baseline: float, rich: RichText
) -> float:
    """Draw un-wrapped styled inline content at (x, baseline). Returns the end x (pen advance)."""
    for tok in tokenize_rich(rich):
        if tok.kind != "space":
            draw_token(base, draw, x, baseline, tok)
        x += tok.width
    return x


@dataclass
class Block:
    width: int  # content wrap width (excludes padding)
    padding: int = 12
    line_height_scale: float = 1.35
    background: tuple[int, int, int, int] = (0, 0, 0, 0)


def _line_box(line: list[Token], scale: float) -> tuple[int, int, int]:
    """(box_height, lead_before_ascent, ascent) for a laid-out line."""
    a, d = line_metrics(line)
    box = round((a + d) * scale)
    lead = (box - (a + d)) // 2
    return box, lead, a


def new_panel_image(
    block: Block, boxes: Sequence[tuple[int, ...]]
) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Allocate the panel image sized to `block` + wrapped-line `boxes`; return (img, draw, first-line y)."""
    w = block.width + 2 * block.padding
    h = 2 * block.padding + sum(b[0] for b in boxes)
    img = Image.new("RGBA", (w, max(h, 1)), block.background)
    draw = ImageDraw.Draw(img)
    return img, draw, block.padding


def render_rich(rich: RichText, block: Block) -> Image.Image:
    """Wrap and render styled inline content into a fixed-width transparent panel image."""
    tokens = tokenize_rich(rich)
    lines = wrap(tokens, block.width)
    boxes = [_line_box(line, block.line_height_scale) for line in lines]

    img, draw, y = new_panel_image(block, boxes)
    for line, (box, lead, ascent) in zip(lines, boxes, strict=True):
        baseline = y + lead + ascent
        x = float(block.padding)
        for tok in line:
            if tok.kind == "space":
                x += tok.width
                continue
            draw_token(img, draw, x, baseline, tok)
            x += tok.width
        y += box
    return img


# --- back-compat: single-style paragraph ---------------------------------------------------------


def tokenize(text: str, opts) -> list[Token]:
    """Legacy single-style tokenizer (TextOpts) — used by back-compat tests."""
    return _tokenize_span(text, Style(size=opts.size, weight=opts.weight, color=opts.color))


def render_paragraph(text: str, block: Block, opts=None) -> Image.Image:
    from overlay.render.text import TextOpts

    opts = opts or TextOpts()
    style = Style(size=opts.size, weight=opts.weight, color=opts.color)
    return render_rich([Span(text, style)], block)
