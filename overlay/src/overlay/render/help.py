"""Pure layout and Pillow rendering for the paged shortcut reference."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from overlay import fonts

HEADER_H = 54
FOOTER_H = 34
ROW_H = 28
PANEL_MAX_W = 760
PANEL_MAX_H = 720
PANEL_MARGIN = 16
BG = (13, 18, 26, 248)
ROW_BG = (25, 33, 45, 235)
KEY_BG = (49, 67, 88, 255)
WHITE = (244, 247, 251, 255)
MUTED = (157, 171, 190, 255)
ACCENT = (113, 190, 255, 255)


@dataclass(frozen=True)
class HelpEntry:
    section: str
    key: str
    label: str
    context: str | None
    source: str


@dataclass(frozen=True)
class HelpSection:
    title: str
    entries: tuple[HelpEntry, ...]


@dataclass(frozen=True)
class HelpPage:
    sections: tuple[HelpSection, ...]
    footer: str


@dataclass(frozen=True)
class HelpDocument:
    pages: tuple[HelpPage, ...]
    width: int
    height: int


def _dimensions(osd: tuple[int, int]) -> tuple[int, int]:
    width = max(240, min(PANEL_MAX_W, osd[0] - PANEL_MARGIN * 2))
    height = max(150, min(PANEL_MAX_H, osd[1] - PANEL_MARGIN * 2))
    return width, height


def _group(entries: tuple[HelpEntry, ...]) -> tuple[tuple[str, tuple[HelpEntry, ...]], ...]:
    groups: list[tuple[str, list[HelpEntry]]] = []
    for entry in entries:
        if not groups or groups[-1][0] != entry.section:
            groups.append((entry.section, []))
        groups[-1][1].append(entry)
    return tuple((title, tuple(items)) for title, items in groups)


def _paginate(
    groups: tuple[tuple[str, tuple[HelpEntry, ...]], ...], *, capacity: int, footer: str
) -> tuple[HelpPage, ...]:
    pages: list[HelpPage] = []
    current: list[HelpSection] = []
    remaining = capacity

    def flush() -> None:
        nonlocal current, remaining
        if current:
            pages.append(HelpPage(tuple(current), footer))
        current = []
        remaining = capacity

    for title, entries in groups:
        offset = 0
        while offset < len(entries):
            if remaining < 2:
                flush()
            take = min(len(entries) - offset, remaining - 1)
            current.append(HelpSection(title, entries[offset : offset + take]))
            remaining -= take + 1
            offset += take
            if offset < len(entries):
                flush()
    flush()
    return tuple(pages) or (HelpPage((), footer),)


def build_document(
    entries: tuple[HelpEntry, ...], *, osd: tuple[int, int], footer: str
) -> HelpDocument:
    width, height = _dimensions(osd)
    capacity = max(2, (height - HEADER_H - FOOTER_H) // ROW_H)
    return HelpDocument(_paginate(_group(entries), capacity=capacity, footer=footer), width, height)


def _font(size: int, weight: int = 400):
    return fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], size, weight))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    suffix = "…"
    while text and draw.textlength(text + suffix, font=font) > width:
        text = text[:-1]
    return text + suffix


def render_page(page: HelpPage, *, width: int, height: int, index: int, total: int) -> Image.Image:
    image = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(image)
    title_font = _font(22, 650)
    section_font = _font(13, 650)
    body_font = _font(16)
    small_font = _font(12)
    draw.text((16, 26), "Saitenka shortcuts", font=title_font, fill=WHITE, anchor="lm")
    draw.text((width - 16, 26), f"{index + 1} / {total}", font=small_font, fill=MUTED, anchor="rm")
    y = HEADER_H
    for section in page.sections:
        draw.text(
            (16, y + ROW_H // 2),
            section.title.upper(),
            font=section_font,
            fill=ACCENT,
            anchor="lm",
        )
        y += ROW_H
        for entry in section.entries:
            draw.rounded_rectangle((12, y + 2, width - 12, y + ROW_H - 2), radius=6, fill=ROW_BG)
            key_width = min(148, max(52, round(draw.textlength(entry.key, font=small_font)) + 20))
            draw.rounded_rectangle(
                (18, y + 5, 18 + key_width, y + ROW_H - 5), radius=5, fill=KEY_BG
            )
            draw.text(
                (18 + key_width // 2, y + ROW_H // 2),
                entry.key,
                font=small_font,
                fill=WHITE,
                anchor="mm",
            )
            badge = "mpv" if entry.source == "mpv" else entry.context
            badge_width = 0
            if badge:
                badge_width = min(124, round(draw.textlength(badge, font=small_font)) + 16)
                bx = width - badge_width - 18
                draw.text(
                    (bx + badge_width // 2, y + ROW_H // 2),
                    badge,
                    font=small_font,
                    fill=MUTED,
                    anchor="mm",
                )
            text_x = 18 + key_width + 12
            label_width = width - text_x - badge_width - 28
            label = _ellipsize(draw, entry.label, body_font, max(24, label_width))
            draw.text((text_x, y + ROW_H // 2), label, font=body_font, fill=WHITE, anchor="lm")
            y += ROW_H
    draw.line((12, height - FOOTER_H, width - 12, height - FOOTER_H), fill=(59, 70, 85, 255))
    draw.text(
        (width // 2, height - FOOTER_H // 2),
        page.footer,
        font=small_font,
        fill=MUTED,
        anchor="mm",
    )
    return image
