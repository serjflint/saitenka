"""Post-mine card preview — a **fixed layout** (no card CSS) to verify the mined card.

Purpose is verification, not a study render: does the word / reading / sentence / meaning look right,
is the **image** the right frame, and is the **sound** there. Every field sits at a baked position; we
compose it from our own primitives, so it's fast and airspace-safe (another OSD overlay).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

    from saitenka_tokenize.japanese import Token

from saitenka.app.lookup import furigana
from saitenka.draw.icon_source import Icon, render_icon
from saitenka.model import RGBA, Span, Style
from saitenka.panel import _DEFAULT_THEME, Theme
from saitenka.render.chip import ChipStyle, render_chip
from saitenka.render.flow import render_flow
from saitenka.render.layout import Block
from saitenka.render.sc_adapter import inline_flow

RED: RGBA = (200, 60, 60, 255)
OUTLINE: RGBA = (200, 200, 200, 255)  # thumbnail / screenshot border
DUP_CHIP: RGBA = (120, 90, 150, 255)  # the ＋ "add anyway" affordance
DUP_LABEL = "＋ add anyway (different scene)"
CLOSE = 36  # ✕ close button size
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
    merge_offer: object | None = None
    merge_pending: bool = False


@dataclass
class PreviewRender:
    """The composed preview plus its clickable regions (panel-space rects, None if absent)."""

    image: Image.Image
    close_rect: Rect | None = None  # ✕ dismiss
    audio_rect: Rect | None = None  # ▶ play the mined clip
    image_rect: Rect | None = None  # the screenshot thumbnail (click to enlarge / shrink)
    merge_rect: Rect | None = None
    dup_rect: Rect | None = None  # ＋ add a second card for a different scene ('exists' only)


@dataclass
class PreviewPanel:
    """What one paint of the preview produced, plus the media the mine captured for it.

    The same cut the picker and the sidebar make: `Owner.INTERACTION`'s slice holds what was
    *decided* — what is composed, its clip, whether it is enlarged — and this holds the screen
    rects the click handler tests against and the clip's live player, which no reducer can kill.

    `last_jpg` / `last_audio` are the *mine's* output, not the shown preview's: the miner captures
    them and the next `build_preview` reads them. The clip the shown preview replays is the slice's
    — two lifetimes, so two fields, or a dismiss cannot drop one and keep the other.
    """

    last_jpg: Path | None = None  # the mpv still (mining fallback image); drives the preview
    last_audio: Path | str | None = None  # the clip the mine encoded, for the next preview built
    audio_proc: subprocess.Popen | None = None  # the ▶ clip's live player, killed on every dismiss
    dup_tok: Token | None = None  # token behind an "exists" preview, for "add anyway"
    # Screen-space clickable regions of the shown panel (None when hidden / absent).
    rect: Rect | None = None
    close_rect: Rect | None = None
    audio_rect: Rect | None = None
    image_rect: Rect | None = None
    dup_rect: Rect | None = None
    merge_rect: Rect | None = None
    #: Whether the preview's keys are currently bound in mpv, so a dismiss on a cue with no preview
    #: up does not send a `keybind` command per cue.
    keys_grabbed: bool = False

    def clear(self) -> None:
        """Dismiss: forget every clickable region in one move."""
        self.rect = self.close_rect = None
        self.audio_rect = self.image_rect = self.dup_rect = self.merge_rect = None


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


def _audio_chip(pv: PreviewData, size: int = 20) -> Image.Image:
    txt = (
        (f"▶ {pv.audio_seconds:.1f}s" if pv.audio_seconds else "▶ audio")
        if pv.audio_seconds is not None
        else "no audio"
    )
    bg = (70, 90, 120, 255) if pv.audio_seconds is not None else (150, 90, 90, 255)
    return render_chip(txt, ChipStyle(size=size, bg=bg)).image


def _preview_actions(pv: PreviewData, theme: Theme, cw: int, media_height: int):
    if pv.status != "exists":
        return [], None, None
    labels = [DUP_LABEL]
    if pv.merge_offer is not None:
        labels.append("Cancel preparation" if pv.merge_pending else "＋ merge scene into this note")
    rows, rects = [], []
    y = media_height + theme.px(8)
    for label in labels:
        chip = render_chip(label, ChipStyle(size=theme.px(18), bg=DUP_CHIP)).image
        row = Image.new("RGBA", (cw, chip.height), (0, 0, 0, 0))
        row.alpha_composite(chip)
        rows.append(row)
        rects.append((0, y, *chip.size))
        y += chip.height + theme.px(8)
    return rows, rects[0], rects[1] if len(rects) > 1 else None


def _media_row(
    pv: PreviewData, theme: Theme, cw: int, *, zoom: bool
) -> tuple[Image.Image, Rect | None, Rect | None]:
    """Screenshot (thumbnail, or enlarged when zoomed) beside the ▶ audio chip. Returns the row plus
    its (image_rect, audio_rect) in row-local coords."""
    a_chip = _audio_chip(pv, theme.px(20))
    if zoom and pv.image is not None:  # enlarged screenshot stacked over the audio chip
        big = pv.image.convert("RGBA").copy()
        big.thumbnail((theme.px(ZOOM_MAX[0]), theme.px(ZOOM_MAX[1])))
        media = Image.new("RGBA", (cw, big.height + theme.px(8) + a_chip.height), (0, 0, 0, 0))
        media.alpha_composite(big, (0, 0))
        ImageDraw.Draw(media).rounded_rectangle(
            [0, 0, big.width - 1, big.height - 1], radius=theme.px(4), outline=OUTLINE
        )
        ay = big.height + theme.px(8)
        media.alpha_composite(a_chip, (0, ay))
        return media, (0, 0, big.width, big.height), (0, ay, a_chip.width, a_chip.height)
    media_h, top = theme.px(118), theme.px(4)
    media = Image.new("RGBA", (cw, media_h), (0, 0, 0, 0))
    x, image_rect_local = 0, None
    if pv.image is not None:
        thumb = pv.image.convert("RGBA").copy()
        thumb.thumbnail((theme.px(196), theme.px(110)))
        media.alpha_composite(thumb, (0, top))
        ImageDraw.Draw(media).rounded_rectangle(
            [0, top, thumb.width - 1, top + thumb.height - 1],
            radius=theme.px(4),
            outline=(200, 200, 200, 255),
        )
        image_rect_local = (0, top, thumb.width, thumb.height)
        x = thumb.width + theme.px(14)
    ay = (media_h - a_chip.height) // 2
    media.alpha_composite(a_chip, (x, ay))
    return media, image_rect_local, (x, ay, a_chip.width, a_chip.height)


def render_card_preview(
    pv: PreviewData, width: int = 460, theme: Theme = _DEFAULT_THEME, *, zoom: bool = False
) -> PreviewRender:
    m, cw = theme.margin, width - 2 * theme.margin
    close = theme.px(CLOSE)  # ✕ button, scaled with the window like the tooltip
    gap = theme.px(8)
    rows: list[Image.Image] = []

    def flow_row(spans, scale=1.3):
        return render_flow(
            spans, Block(width=cw, padding=0, line_height_scale=scale, background=(0, 0, 0, 0))
        )

    # header: status chip + big ruby headword + ✕ close (top-right)
    label, color = STATUS.get(pv.status, STATUS["mined"])
    chip = render_chip(label, ChipStyle(size=theme.px(18), bg=color)).image
    hw = inline_flow(
        furigana(pv.expression, pv.reading), Style(size=theme.px(34), weight=700, color=theme.text)
    )
    hw_img = render_flow(
        hw,
        Block(
            width=max(1, cw - chip.width - close - theme.px(24)),
            padding=0,
            line_height_scale=1.3,
            background=(0, 0, 0, 0),
        ),
    )
    header = Image.new(
        "RGBA", (cw, max(chip.height, hw_img.height, close) + theme.px(6)), (0, 0, 0, 0)
    )
    header.alpha_composite(chip, (0, (header.height - chip.height) // 2))
    header.alpha_composite(
        hw_img, (chip.width + theme.px(12), (header.height - hw_img.height) // 2)
    )
    button = Image.new("RGBA", (close, close), (0, 0, 0, 0))
    ImageDraw.Draw(button).rounded_rectangle(
        (0, 0, close - 1, close - 1), radius=theme.px(6), fill=(225, 225, 225, 255)
    )
    button.alpha_composite(
        render_icon(Icon.CLOSE, theme.px(24), color=theme.text), (theme.px(6), theme.px(6))
    )
    header.alpha_composite(button, (cw - close, 0))
    close_rect = (m + cw - close, m, close, close)  # header sits at (m, m)
    rows.append(header)

    # sentence (mined word bolded red)
    rows.append(
        flow_row(
            [
                Span("文  ", Style(size=theme.px(18), color=theme.muted)),
                *_bold_sentence(pv.sentence_lines, pv.mined_surface, theme.px(23), theme.text),
            ]
        )
    )

    # glosses (up to 4 bullets)
    if pv.glosses:
        gspans = []
        for i, g in enumerate(pv.glosses[:4]):
            if i:
                gspans.append(Span("\n", Style(size=theme.px(21))))
            gspans.append(Span(f"・{g}", Style(size=theme.px(21), color=theme.text)))
        rows.append(flow_row(gspans))

    media, image_rect_local, audio_rect_local = _media_row(pv, theme, cw, zoom=zoom)
    media_idx = len(rows)
    rows.append(media)

    action_rows, dup_local, merge_local = _preview_actions(pv, theme, cw, media.height)
    rows.extend(action_rows)

    if pv.footer:
        rows.append(flow_row([Span(pv.footer, Style(size=theme.px(15), color=theme.muted))]))

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

    return PreviewRender(
        canvas,
        close_rect,
        _abs(audio_rect_local),
        _abs(image_rect_local),
        _abs(merge_local),
        _abs(dup_local),
    )
