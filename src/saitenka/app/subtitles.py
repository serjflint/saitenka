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

from saitenka import fonts
from saitenka.model import Span, Style
from saitenka.render.flow import render_flow
from saitenka.render.layout import NO_START, Block

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka_tokenize.japanese import Token

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
    #: How the native path drew this token — its face and its size in frame pixels. Empty from the
    #: legacy renderer, which paints the color itself and has nothing to overprint.
    font_name: str = ""
    font_size: float = 0.0
    #: The token's anti-aliased coverage over its own rect, one byte per pixel, kept only when the
    #: face is one the text device cannot draw — see `TokenGeometry.coverage`.
    coverage: bytes = b""

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


def token_at(
    boxes: Sequence[WordBox],
    point: tuple[float, float],
    origin: tuple[int, int],
    *,
    is_skippable: Callable[[int], bool],
) -> int:
    """Which token a pointer position lands on, or -1.

    A function of the boxes rather than a method on the host, because it is the one thing both
    subtitle engines have to agree about. Their layouts are Pillow and libass and will never agree
    on pixels; what a user notices is a *different word* under the cursor, so that is where the
    differential oracle lives — and an oracle that re-implemented the resolution would be comparing
    two things neither engine does.
    """
    origin_x, origin_y = origin
    for box in boxes:
        if is_skippable(box.index):
            continue
        if box.contains(point[0] - origin_x, point[1] - origin_y):
            return box.index
    return -1


def box_for_token(boxes: list[WordBox], token_index: int) -> WordBox | None:
    """Return the interaction box identified by its token index."""
    return next((box for box in boxes if box.index == token_index), None)


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
    tab_width = px(76)
    tab_x = width - (tab_width * 3 + px(6) * 2 + px(8))
    for label, tab_view in (("Track", "track"), ("Backlog", "backlog"), ("Mine", "mine")):
        selected = view == tab_view
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
        status_text = _ellipsize(draw, row.status, small_font, px(188))
        status_width = min(
            px(204),
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
        return "\\ toggles  ·  click Track/Backlog/Mine"
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


def render_picker(
    rows: list[SidebarRow],
    *,
    width: int,
    height: int,
    title: str = "Subtitle sources",
    message: str | None = None,
    footer: str,
    scale: float = 1.0,
) -> SidebarRender:
    """Render Window 1's download picker: a titled list of jimaku candidates, best-match first.
    Reuses the sidebar row renderer (so per-row download hitboxes + status pills come for free); the
    header is a plain title instead of the sidebar's Track/Backlog tabs. ``message`` (loading / error
    / empty) replaces the row area when there's nothing to list."""

    def px(value: int) -> int:
        return max(1, round(value * scale))

    width, height = max(320, width), max(180, height)
    image = Image.new("RGBA", (width, height), SIDEBAR_BG)
    draw = ImageDraw.Draw(image)
    title_font = _font(px(22))
    body_font = _font(px(18))
    small_font = _font(px(14))
    header_h, footer_h, row_h = px(52), px(28), px(54)
    capacity = max(1, (height - header_h - footer_h) // row_h)

    draw.text((px(14), px(16)), title, font=title_font, fill=WHITE, anchor="lm")
    hits: list[SidebarHitBox] = []
    if message:
        draw.text(
            (width // 2, height // 2), message, font=body_font, fill=SIDEBAR_MUTED, anchor="mm"
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
    draw.text((px(14), height - px(14)), footer, font=small_font, fill=SIDEBAR_MUTED, anchor="lm")
    return SidebarRender(image, tuple(hits), capacity)


def render_plain_subtitle(
    text: str, width: int, *, size: int, background: tuple[int, int, int, int] = BOX
) -> SubtitleRender:
    """Render a non-Japanese primary track without tokenization or interactive hitboxes."""
    normalized = text.replace("\\N", " ").replace("\n", " ").strip()
    style = Style(size=size, color=WHITE)
    image = render_flow(
        [Span(normalized, style)],
        Block(width=max(1, round(width * 0.86)), padding=14, background=background),
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


@dataclass(frozen=True)
class _SubStyle:
    """Render-constant draw style + layout geometry for one subtitle raster — computed once from the
    fitted font, shared by every line and token draw (so neither helper carries a font/metric clump)."""

    font: object
    size: int
    stroke: int
    ascent: int
    text_h: int
    pad_x: int
    pad_y: int
    row_h: int
    line_gap: int
    img_w: int


@dataclass(frozen=True)
class _Place:
    """Where one token draws within the raster (flat index + pixel position)."""

    gi: int
    x: float
    y: int
    baseline: float
    w: float


def _draw_token(draw, tok: Token, place: _Place, style, st: _SubStyle, *, hovered: bool) -> WordBox:
    """Draw one token's glyphs (+ its JLPT underline, if any) at ``place`` and return its hit box. The
    hovered token takes the highlight color; else the style's color (or plain white)."""
    color = HOVER if hovered else (style.color if style else WHITE)
    draw.text(
        (place.x, place.baseline),
        tok.surface,
        font=st.font,
        fill=color,
        anchor="ls",
        stroke_width=st.stroke,
        stroke_fill=OUTLINE,
    )
    underline = style.underline if style else None
    if underline is not None:
        uy = place.baseline + max(2, round(st.size * 0.10))
        draw.line(
            [(place.x, uy), (place.x + place.w, uy)], fill=underline, width=max(2, st.size // 14)
        )
    return WordBox(place.gi, int(place.x), place.y + st.pad_y, int(place.w), st.text_h)


def _draw_visual_lines(
    img: Image.Image,
    visual_lines: list[list[tuple[int, Token, float]]],
    line_widths: list[float],
    st: _SubStyle,
    *,
    styles: list | None,
    hover: int | None,
    hover_end: int | None = None,
    background: tuple[int, int, int, int] = BOX,
) -> list[WordBox]:
    hi_end = hover_end if hover_end is not None else (hover + 1 if hover is not None else 0)
    draw = ImageDraw.Draw(img)
    boxes: list[WordBox] = []
    y = 0
    for vl, lw in zip(visual_lines, line_widths, strict=True):
        left = (st.img_w - lw) // 2  # centre each line
        draw.rounded_rectangle(
            [left, y, left + lw - 1, y + st.row_h - 1], radius=10, fill=background
        )
        x = float(left + st.pad_x)
        baseline = y + st.pad_y + st.ascent
        for gi, tok, w in vl:
            style = styles[gi] if styles and gi < len(styles) else None
            hovered = hover is not None and hover <= gi < hi_end
            boxes.append(
                _draw_token(draw, tok, _Place(gi, x, y, baseline, w), style, st, hovered=hovered)
            )
            x += w
        y += st.row_h + st.line_gap
    return boxes


@dataclass(frozen=True)
class SubtitleSpacing:
    """Padding + inter-line gap for :func:`render_subtitle` (px) — the layout constants a caller can
    override as one value."""

    pad_x: int = 20
    pad_y: int = 8
    line_gap: int = 5


def render_subtitle(
    lines: list[list[Token]],
    osd_w: int,
    size: int = 44,
    hover: int | None = None,
    hover_end: int | None = None,
    styles: list | None = None,
    *,
    spacing: SubtitleSpacing | None = None,
    background: tuple[int, int, int, int] = BOX,
) -> SubtitleRender:
    """`lines` is a list of source lines (each a token list); global token index is row-major.

    `styles` (optional, indexed by global token index) gives each token a text color and an optional
    JLPT underline color; the hovered token overrides the text color with the highlight. A merged
    multi-token term highlights the whole span ``[hover, hover_end)`` (defaults to a single token).
    """
    spacing = spacing or SubtitleSpacing()
    max_w = osd_w * 0.94
    font, size, measured = _fit_font_size(lines, max_w, spacing.pad_x, size)

    ascent, descent = font.getmetrics()
    text_h = ascent + descent
    row_h = text_h + 2 * spacing.pad_y

    # wrap each source line into visual lines
    visual_lines: list[list[tuple[int, Token, float]]] = []
    for row in measured:
        visual_lines.extend(_wrap_line(row, max_w - 2 * spacing.pad_x))
    if not visual_lines:
        return SubtitleRender(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), [])

    line_widths = [sum(w for _, _, w in vl) + 2 * spacing.pad_x for vl in visual_lines]
    img_w = int(max(line_widths))
    img_h = len(visual_lines) * row_h + (len(visual_lines) - 1) * spacing.line_gap
    img = Image.new("RGBA", (max(img_w, 1), max(img_h, 1)), (0, 0, 0, 0))
    stroke = max(1, size // 16)

    st = _SubStyle(
        font=font,
        size=size,
        stroke=stroke,
        ascent=ascent,
        text_h=text_h,
        pad_x=spacing.pad_x,
        pad_y=spacing.pad_y,
        row_h=row_h,
        line_gap=spacing.line_gap,
        img_w=img_w,
    )
    boxes = _draw_visual_lines(
        img,
        visual_lines,
        line_widths,
        st,
        styles=styles,
        hover=hover,
        hover_end=hover_end,
        background=background,
    )
    return SubtitleRender(img, boxes)
