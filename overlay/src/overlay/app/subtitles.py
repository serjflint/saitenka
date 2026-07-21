"""Render a (possibly multi-line) subtitle SubMiner-style and expose per-word hitboxes.

Subtitle lines can be long and can carry explicit breaks, so we honour ``\\n`` and wrap each source
line to the screen width. Every token gets a pixel rect **on its own visual line**, so the controller
can anchor the tooltip above the word's line (not the whole block). White text with an outline over a
per-line translucent rounded box; the hovered word is tinted.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.app.tokenize import Token
from overlay.render.layout import NO_START

WHITE = (255, 255, 255, 255)
HOVER = (255, 214, 90, 255)  # warm yellow highlight
OUTLINE = (0, 0, 0, 255)
BOX = (0, 0, 0, 150)  # translucent backing


@dataclass(frozen=True)
class WordBox:
    index: int  # global (flat) token index
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass
class SubtitleRender:
    image: Image.Image
    boxes: list[WordBox]


def _font(size: int):
    return fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], size, 500))


def _wrap_line(line: list[tuple[int, Token, float]], max_w: float) -> list[list]:
    """Greedily wrap a source line's (idx, token, width) into visual lines ≤ max_w (light kinsoku)."""
    visual: list[list] = []
    cur: list = []
    x = 0.0
    for item in line:
        _, tok, w = item
        if cur and x + w > max_w and tok.surface[:1] not in NO_START:
            visual.append(cur)
            cur, x = [], 0.0
        cur.append(item)
        x += w
    if cur:
        visual.append(cur)
    return visual


def render_subtitle(
    lines: list[list[Token]],
    osd_w: int,
    size: int = 44,
    hover: int | None = None,
    styles: list | None = None,
    pad_x: int = 20,
    pad_y: int = 8,
    line_gap: int = 5,
) -> SubtitleRender:
    """`lines` is a list of source lines (each a token list); global token index is row-major.

    `styles` (optional, indexed by global token index) gives each token a text color and an optional
    JLPT underline color; the hovered token overrides the text color with the highlight.
    """
    max_w = osd_w * 0.94

    # measure with global indices, shrinking if even one token is wider than the screen
    for _ in range(8):
        font = _font(size)
        measured: list[list[tuple[int, Token, float]]] = []
        gi = 0
        widest = 0.0
        for line in lines:
            row = []
            for tok in line:
                w = font.getlength(tok.surface)
                widest = max(widest, w)
                row.append((gi, tok, w))
                gi += 1
            measured.append(row)
        if widest + 2 * pad_x <= max_w or size <= 20:
            break
        size = max(20, int(size * max_w / (widest + 2 * pad_x)))

    font = _font(size)
    ascent, descent = font.getmetrics()
    text_h = ascent + descent
    row_h = text_h + 2 * pad_y

    # wrap each source line into visual lines
    visual_lines: list[list[tuple[int, Token, float]]] = []
    for row in measured:
        visual_lines.extend(_wrap_line(row, max_w - 2 * pad_x))
    if not visual_lines:
        return SubtitleRender(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), [])

    line_widths = [sum(w for _, _, w in vl) + 2 * pad_x for vl in visual_lines]
    img_w = int(max(line_widths))
    img_h = len(visual_lines) * row_h + (len(visual_lines) - 1) * line_gap
    img = Image.new("RGBA", (max(img_w, 1), max(img_h, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    stroke = max(1, size // 16)

    boxes: list[WordBox] = []
    y = 0
    for vl, lw in zip(visual_lines, line_widths, strict=True):
        left = (img_w - lw) // 2  # centre each line
        draw.rounded_rectangle([left, y, left + lw - 1, y + row_h - 1], radius=10, fill=BOX)
        x = float(left + pad_x)
        baseline = y + pad_y + ascent
        for gi, tok, w in vl:
            st = styles[gi] if styles and gi < len(styles) else None
            color = HOVER if gi == hover else (st.color if st else WHITE)
            underline = st.underline if st else None
            draw.text(
                (x, baseline),
                tok.surface,
                font=font,
                fill=color,
                anchor="ls",
                stroke_width=stroke,
                stroke_fill=OUTLINE,
            )
            if underline is not None:
                uy = baseline + max(2, round(size * 0.10))
                draw.line([(x, uy), (x + w, uy)], fill=underline, width=max(2, size // 14))
            boxes.append(WordBox(gi, int(x), y + pad_y, int(w), text_h))
            x += w
        y += row_h + line_gap
    return SubtitleRender(img, boxes)
