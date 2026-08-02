"""Render a (possibly multi-line) subtitle SubMiner-style and expose per-word hitboxes.

Subtitle lines can be long and can carry explicit breaks, so we honour ``\\n`` and wrap each source
line to the screen width. Every token gets a pixel rect **on its own visual line**, so the controller
can anchor the tooltip above the word's line (not the whole block). White text with an outline over a
per-line translucent rounded box; the hovered word is tinted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from overlay import fonts
from overlay.model import Span, Style
from overlay.render.flow import render_flow
from overlay.render.layout import NO_START, Block

if TYPE_CHECKING:
    from overlay.app.tokenize import Token

WHITE = (255, 255, 255, 255)
HOVER = (255, 214, 90, 255)  # warm yellow highlight
OUTLINE = (0, 0, 0, 255)
BOX = (0, 0, 0, 150)  # translucent backing
SIDEBAR_BG = (13, 18, 26, 238)
SIDEBAR_ROW = (27, 35, 47, 225)
SIDEBAR_ACTIVE = (47, 75, 103, 245)
SIDEBAR_MUTED = (155, 169, 187, 255)
SIDEBAR_STATUS = {
    "open": (154, 111, 24, 255),
    "reviewed": (43, 94, 145, 255),
    "mined": (31, 111, 75, 255),
    "archived": (70, 79, 91, 255),
}


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


@dataclass(frozen=True)
class SidebarAction:
    label: str
    kind: str
    value: int


@dataclass(frozen=True)
class SidebarRow:
    value: int
    timestamp: str
    text: str
    parts: tuple[tuple[str, tuple[int, int, int, int]], ...] = ()
    status: str | None = None
    active: bool = False
    click_kind: str | None = None
    actions: tuple[SidebarAction, ...] = ()


@dataclass(frozen=True)
class SidebarHitBox:
    kind: str
    value: int
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass(frozen=True)
class SidebarRender:
    image: Image.Image
    hitboxes: tuple[SidebarHitBox, ...]
    row_capacity: int


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    text = text.replace("\\N", " ").replace("\n", " ").strip()
    if draw.textlength(text, font=font) <= width:
        return text
    suffix = "…"
    while text and draw.textlength(text + suffix, font=font) > width:
        text = text[:-1]
    return text + suffix


def _draw_sidebar_header(
    draw: ImageDraw.ImageDraw, width: int, view: str, title_font, small_font, scale: float
) -> list[SidebarHitBox]:
    def px(value: int) -> int:
        return max(1, round(value * scale))

    draw.text((px(14), px(16)), "Subtitles", font=title_font, fill=WHITE, anchor="lm")
    hits: list[SidebarHitBox] = []
    tab_x = width - px(178)
    for label, tab_view in (("Track", "track"), ("Backlog", "backlog")):
        selected = view == tab_view
        tab_width = px(82)
        draw.rounded_rectangle(
            (tab_x, px(10), tab_x + tab_width, px(42)),
            radius=px(8),
            fill=SIDEBAR_ACTIVE if selected else SIDEBAR_ROW,
        )
        draw.text((tab_x + tab_width // 2, px(26)), label, font=small_font, fill=WHITE, anchor="mm")
        hits.append(SidebarHitBox(f"view:{tab_view}", 0, tab_x, px(10), tab_width, px(32)))
        tab_x += tab_width + px(6)
    return hits


def _draw_sidebar_row(
    draw: ImageDraw.ImageDraw,
    row: SidebarRow,
    *,
    width: int,
    y: int,
    row_height: int,
    body_font,
    small_font,
    scale: float,
) -> list[SidebarHitBox]:
    def px(value: int) -> int:
        return max(1, round(value * scale))

    draw.rectangle(
        (px(8), y + px(2), width - px(8), y + row_height - px(2)),
        fill=SIDEBAR_ACTIVE if row.active else SIDEBAR_ROW,
    )
    hits: list[SidebarHitBox] = []
    action_width = 0
    action_x = width - px(16)
    for action in reversed(row.actions):
        action_x -= px(34)
        draw.rounded_rectangle(
            (action_x, y + px(11), action_x + px(28), y + px(41)),
            radius=px(7),
            fill=(61, 78, 98, 255),
        )
        draw.text(
            (action_x + px(14), y + px(26)),
            action.label,
            font=small_font,
            fill=WHITE,
            anchor="mm",
        )
        hits.append(SidebarHitBox(action.kind, action.value, action_x, y + px(8), px(30), px(36)))
        action_x -= px(4)
        action_width += px(34)
    status_width = 0
    if row.status:
        status_text = _ellipsize(draw, row.status, small_font, px(132))
        status_width = min(
            px(148),
            max(px(44), round(draw.textlength(status_text, font=small_font)) + px(16)),
        )
        status_x = width - action_width - status_width - px(12)
        status_color = SIDEBAR_STATUS.get(row.status, (75, 66, 112, 255))
        draw.rounded_rectangle(
            (status_x, y + px(14), status_x + status_width, y + px(40)),
            radius=px(7),
            fill=status_color,
        )
        draw.text(
            (status_x + status_width // 2, y + px(27)),
            status_text,
            font=small_font,
            fill=WHITE,
            anchor="mm",
        )
    draw.text((px(16), y + px(27)), row.timestamp, font=small_font, fill=SIDEBAR_MUTED, anchor="lm")
    text_x = px(86)
    reserved_width = action_width + status_width
    remaining = max(px(30), width - text_x - reserved_width - px(18))
    x = text_x
    for text, color in row.parts or ((row.text, WHITE),):
        shown = _ellipsize(draw, text, body_font, remaining)
        if not shown:
            break
        draw.text((x, y + px(27)), shown, font=body_font, fill=color, anchor="lm")
        advance = round(draw.textlength(shown, font=body_font))
        x += advance
        remaining -= advance
        if shown.endswith("…") or remaining <= 0:
            break
    if row.click_kind:
        hits.append(
            SidebarHitBox(
                row.click_kind,
                row.value,
                px(8),
                y + px(2),
                width - reserved_width - px(20),
                row_height - px(4),
            )
        )
    return hits


def _sidebar_footer(total: int, first: int, visible: int) -> str:
    if not total:
        return "\\ toggles  ·  click Track/Backlog"
    end = min(total, first + visible)
    return f"{first + 1}–{end} / {total}  ·  wheel to scroll"


def render_sidebar(
    rows: list[SidebarRow],
    *,
    width: int,
    height: int,
    view: str,
    total: int,
    first: int,
    unavailable: str | None = None,
    scale: float = 1.0,
) -> SidebarRender:
    """Render only the supplied viewport rows; coordinates are panel-local."""

    def px(value: int) -> int:
        return max(1, round(value * scale))

    width, height = max(320, width), max(180, height)
    image = Image.new("RGBA", (width, height), SIDEBAR_BG)
    draw = ImageDraw.Draw(image)
    title_font = _font(px(22))
    body_font = _font(px(18))
    small_font = _font(px(14))
    hits: list[SidebarHitBox] = []
    header_h, footer_h, row_h = px(52), px(28), px(54)
    capacity = max(1, (height - header_h - footer_h) // row_h)

    hits.extend(_draw_sidebar_header(draw, width, view, title_font, small_font, scale))

    if unavailable:
        draw.text(
            (width // 2, height // 2),
            unavailable,
            font=body_font,
            fill=SIDEBAR_MUTED,
            anchor="mm",
        )
    for position, row in enumerate(rows[:capacity]):
        y = header_h + position * row_h
        hits.extend(
            _draw_sidebar_row(
                draw,
                row,
                width=width,
                y=y,
                row_height=row_h,
                body_font=body_font,
                small_font=small_font,
                scale=scale,
            )
        )

    footer = _sidebar_footer(total, first, len(rows))
    draw.text((px(14), height - px(14)), footer, font=small_font, fill=SIDEBAR_MUTED, anchor="lm")
    return SidebarRender(image, tuple(hits), capacity)


def render_plain_subtitle(text: str, width: int, *, size: int) -> SubtitleRender:
    """Render a non-Japanese primary track without tokenization or interactive hitboxes."""
    normalized = text.replace("\\N", " ").replace("\n", " ").strip()
    style = Style(size=size, color=WHITE)
    image = render_flow(
        [Span(normalized, style)],
        Block(width=max(1, round(width * 0.86)), padding=14, background=BOX),
    )
    return SubtitleRender(image, [])


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


def _fit_font_size(lines: list[list[Token]], max_w: float, pad_x: int, size: int):
    """Shrink ``size`` (down to a floor of 20) until every measured token fits within ``max_w``
    (minus padding), or 8 attempts are exhausted. Returns ``(font, size, measured)`` — ``measured``
    carries (global_index, token, width) triples with global (row-major) indices already assigned,
    so the caller never re-measures."""
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
    return font, size, measured


def _draw_token(
    draw: ImageDraw.ImageDraw,
    tok: Token,
    x: float,
    baseline: float,
    w: float,
    *,
    gi: int,
    y: int,
    pad_y: int,
    text_h: int,
    style,
    hovered: bool,
    font,
    size: int,
    stroke: int,
) -> WordBox:
    """Draw one token's glyphs (+ its JLPT underline, if any) at ``x``/``baseline`` and return its
    hit box. The hovered token takes the highlight color; else the style's color (or plain white)."""
    color = HOVER if hovered else (style.color if style else WHITE)
    draw.text(
        (x, baseline),
        tok.surface,
        font=font,
        fill=color,
        anchor="ls",
        stroke_width=stroke,
        stroke_fill=OUTLINE,
    )
    underline = style.underline if style else None
    if underline is not None:
        uy = baseline + max(2, round(size * 0.10))
        draw.line([(x, uy), (x + w, uy)], fill=underline, width=max(2, size // 14))
    return WordBox(gi, int(x), y + pad_y, int(w), text_h)


def _draw_visual_lines(
    img: Image.Image,
    visual_lines: list[list[tuple[int, Token, float]]],
    line_widths: list[float],
    *,
    img_w: int,
    row_h: int,
    line_gap: int,
    pad_x: int,
    pad_y: int,
    ascent: int,
    text_h: int,
    font,
    size: int,
    stroke: int,
    styles: list | None,
    hover: int | None,
    hover_end: int | None = None,
) -> list[WordBox]:
    hi_end = hover_end if hover_end is not None else (hover + 1 if hover is not None else 0)
    draw = ImageDraw.Draw(img)
    boxes: list[WordBox] = []
    y = 0
    for vl, lw in zip(visual_lines, line_widths, strict=True):
        left = (img_w - lw) // 2  # centre each line
        draw.rounded_rectangle([left, y, left + lw - 1, y + row_h - 1], radius=10, fill=BOX)
        x = float(left + pad_x)
        baseline = y + pad_y + ascent
        for gi, tok, w in vl:
            st = styles[gi] if styles and gi < len(styles) else None
            hovered = hover is not None and hover <= gi < hi_end
            boxes.append(
                _draw_token(
                    draw,
                    tok,
                    x,
                    baseline,
                    w,
                    gi=gi,
                    y=y,
                    pad_y=pad_y,
                    text_h=text_h,
                    style=st,
                    hovered=hovered,
                    font=font,
                    size=size,
                    stroke=stroke,
                )
            )
            x += w
        y += row_h + line_gap
    return boxes


def render_subtitle(
    lines: list[list[Token]],
    osd_w: int,
    size: int = 44,
    hover: int | None = None,
    hover_end: int | None = None,
    styles: list | None = None,
    pad_x: int = 20,
    pad_y: int = 8,
    line_gap: int = 5,
) -> SubtitleRender:
    """`lines` is a list of source lines (each a token list); global token index is row-major.

    `styles` (optional, indexed by global token index) gives each token a text color and an optional
    JLPT underline color; the hovered token overrides the text color with the highlight. A merged
    multi-token term highlights the whole span ``[hover, hover_end)`` (defaults to a single token).
    """
    max_w = osd_w * 0.94
    font, size, measured = _fit_font_size(lines, max_w, pad_x, size)

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
    stroke = max(1, size // 16)

    boxes = _draw_visual_lines(
        img,
        visual_lines,
        line_widths,
        img_w=img_w,
        row_h=row_h,
        line_gap=line_gap,
        pad_x=pad_x,
        pad_y=pad_y,
        ascent=ascent,
        text_h=text_h,
        font=font,
        size=size,
        stroke=stroke,
        styles=styles,
        hover=hover,
        hover_end=hover_end,
    )
    return SubtitleRender(img, boxes)
