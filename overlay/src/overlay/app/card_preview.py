"""Post-mine card preview — a **fixed layout** (no card CSS) to verify the mined card.

Purpose is verification, not a study render: does the word / reading / sentence / meaning look right,
is the **image** the right frame, and is the **sound** there. Every field sits at a baked position; we
compose it from our own primitives, so it's fast and airspace-safe (another OSD overlay).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from overlay.app.lookup import furigana
from overlay.draw.chip import ChipStyle, render_chip
from overlay.draw.icons import cross
from overlay.model import RGBA, Span, Style
from overlay.panel import _DEFAULT_THEME, Theme
from overlay.render.flow import render_flow
from overlay.render.layout import Block
from overlay.sc.walk import inline_flow

RED: RGBA = (200, 60, 60, 255)
CLOSE = 20  # ✕ close button size
ZOOM_MAX = (700, 620)  # enlarged screenshot bounds (verify the frame)
STATUS = {
    "mined": ("✚ mined", (90, 150, 90, 255)),
    "exists": ("✓ in deck", (80, 120, 190, 255)),
    "duplicate": ("• duplicate", (170, 140, 60, 255)),
}

Rect = tuple[int, int, int, int]


@dataclass
class PreviewData:
    status: str  # 'mined' | 'exists' | 'duplicate'
    expression: str
    reading: str
    sentence_lines: list[str]
    mined_surface: str = ""
    glosses: list[str] = field(default_factory=list)
    image: Image.Image | None = None
    audio_seconds: float | None = None
    footer: str = ""


@dataclass
class PreviewRender:
    """The composed preview plus its clickable regions (panel-space rects, None if absent)."""

    image: Image.Image
    close_rect: Rect | None = None  # ✕ dismiss
    audio_rect: Rect | None = None  # ▶ play the mined clip
    image_rect: Rect | None = None  # the screenshot thumbnail (click to enlarge / shrink)


def _bold_sentence(lines: list[str], surface: str, size: int, color: RGBA) -> list:
    spans: list = []
    for li, line in enumerate(lines):
        if li:
            spans.append(Span("\n", Style(size=size)))
        i = 0
        while surface:
            j = line.find(surface, i)
            if j < 0:
                break
            if j > i:
                spans.append(Span(line[i:j], Style(size=size, color=color)))
            spans.append(Span(surface, Style(size=size, weight=700, color=RED)))
            i = j + len(surface)
        spans.append(Span(line[i:], Style(size=size, color=color)))
    return spans


def _audio_chip(pv: PreviewData) -> Image.Image:
    txt = (
        (f"▶ {pv.audio_seconds:.1f}s" if pv.audio_seconds else "▶ audio")
        if pv.audio_seconds is not None
        else "no audio"
    )
    bg = (70, 90, 120, 255) if pv.audio_seconds is not None else (150, 90, 90, 255)
    return render_chip(txt, ChipStyle(size=20, bg=bg)).image


def render_card_preview(
    pv: PreviewData, width: int = 460, theme: Theme = _DEFAULT_THEME, zoom: bool = False
) -> PreviewRender:
    m, cw = theme.margin, width - 2 * theme.margin
    rows: list[Image.Image] = []
    image_rect_local: Rect | None = None  # within the media row
    audio_rect_local: Rect | None = None

    def flow_row(spans, scale=1.3):
        return render_flow(
            spans, Block(width=cw, padding=0, line_height_scale=scale, background=(0, 0, 0, 0))
        )

    # header: status chip + big ruby headword + ✕ close (top-right)
    label, color = STATUS.get(pv.status, STATUS["mined"])
    chip = render_chip(label, ChipStyle(size=18, bg=color)).image
    hw = inline_flow(
        furigana(pv.expression, pv.reading), Style(size=34, weight=700, color=theme.text)
    )
    hw_img = flow_row(hw)
    header = Image.new("RGBA", (cw, max(chip.height, hw_img.height) + 6), (0, 0, 0, 0))
    header.alpha_composite(chip, (0, (header.height - chip.height) // 2))
    header.alpha_composite(hw_img, (chip.width + 12, (header.height - hw_img.height) // 2))
    header.alpha_composite(cross(CLOSE), (cw - CLOSE, 0))
    close_rect = (m + cw - CLOSE, m, CLOSE, CLOSE)  # header sits at (m, m)
    rows.append(header)

    # sentence (mined word bolded red)
    rows.append(
        flow_row(
            [
                Span("文  ", Style(size=18, color=theme.muted)),
                *_bold_sentence(pv.sentence_lines, pv.mined_surface, 23, theme.text),
            ]
        )
    )

    # glosses (up to 4 bullets)
    if pv.glosses:
        gspans = []
        for i, g in enumerate(pv.glosses[:4]):
            if i:
                gspans.append(Span("\n", Style(size=21)))
            gspans.append(Span(f"・{g}", Style(size=21, color=theme.text)))
        rows.append(flow_row(gspans))

    # media row: screenshot (thumbnail, or enlarged when zoomed) + audio chip
    a_chip = _audio_chip(pv)
    if zoom and pv.image is not None:  # enlarged screenshot stacked over the audio chip
        big = pv.image.convert("RGBA").copy()
        big.thumbnail(ZOOM_MAX)
        media = Image.new("RGBA", (cw, big.height + 8 + a_chip.height), (0, 0, 0, 0))
        media.alpha_composite(big, (0, 0))
        ImageDraw.Draw(media).rounded_rectangle(
            [0, 0, big.width - 1, big.height - 1], radius=4, outline=(200, 200, 200, 255)
        )
        image_rect_local = (0, 0, big.width, big.height)
        ay = big.height + 8
        media.alpha_composite(a_chip, (0, ay))
        audio_rect_local = (0, ay, a_chip.width, a_chip.height)
    else:
        media = Image.new("RGBA", (cw, 118), (0, 0, 0, 0))
        x = 0
        if pv.image is not None:
            thumb = pv.image.convert("RGBA").copy()
            thumb.thumbnail((196, 110))
            media.alpha_composite(thumb, (0, 4))
            ImageDraw.Draw(media).rounded_rectangle(
                [0, 4, thumb.width - 1, 4 + thumb.height - 1],
                radius=4,
                outline=(200, 200, 200, 255),
            )
            image_rect_local = (0, 4, thumb.width, thumb.height)
            x = thumb.width + 14
        ay = (118 - a_chip.height) // 2
        media.alpha_composite(a_chip, (x, ay))
        audio_rect_local = (x, ay, a_chip.width, a_chip.height)
    media_idx = len(rows)
    rows.append(media)

    if pv.footer:
        rows.append(flow_row([Span(pv.footer, Style(size=15, color=theme.muted))]))

    gap = 8
    total_h = 2 * m + sum(r.height for r in rows) + gap * (len(rows) - 1)
    canvas = Image.new("RGBA", (width, total_h), theme.bg)
    draw = ImageDraw.Draw(canvas)
    y, media_y = m, m
    for i, r in enumerate(rows):
        canvas.alpha_composite(r, (m, y))
        if i == media_idx:
            media_y = y
        y += r.height + gap
        if i == 0:  # divider under the header
            draw.line([(m, y - gap // 2), (width - m, y - gap // 2)], fill=(225, 225, 225, 255))

    def _abs(local: Rect | None) -> Rect | None:
        return (m + local[0], media_y + local[1], local[2], local[3]) if local else None

    return PreviewRender(canvas, close_rect, _abs(audio_rect_local), _abs(image_rect_local))
