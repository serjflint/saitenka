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
    scale: float


def _px(value: int, scale: float) -> int:
    return max(1, round(value * scale))


def _dimensions(osd: tuple[int, int], scale: float) -> tuple[int, int]:
    margin = _px(PANEL_MARGIN, scale)
    width = max(_px(240, scale), min(_px(PANEL_MAX_W, scale), osd[0] - margin * 2))
    height = max(_px(150, scale), min(_px(PANEL_MAX_H, scale), osd[1] - margin * 2))
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
    entries: tuple[HelpEntry, ...], *, osd: tuple[int, int], footer: str, scale: float = 1.0
) -> HelpDocument:
    width, height = _dimensions(osd, scale)
    capacity = max(2, (height - _px(HEADER_H, scale) - _px(FOOTER_H, scale)) // _px(ROW_H, scale))
    return HelpDocument(
        _paginate(_group(entries), capacity=capacity, footer=footer), width, height, scale
    )


def _font(size: int, weight: int = 400):
    return fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], size, weight))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    suffix = "…"
    while text and draw.textlength(text + suffix, font=font) > width:
        text = text[:-1]
    return text + suffix


def render_page(
    page: HelpPage,
    *,
    width: int,
    height: int,
    index: int,
    total: int,
    scale: float = 1.0,
) -> Image.Image:
    image = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(image)

    def px(value: int) -> int:
        return _px(value, scale)

    title_font = _font(px(22), 650)
    section_font = _font(px(13), 650)
    body_font = _font(px(16))
    small_font = _font(px(12))
    row_h, footer_h = px(ROW_H), px(FOOTER_H)
    draw.text((px(16), px(26)), "Saitenka shortcuts", font=title_font, fill=WHITE, anchor="lm")
    draw.text(
        (width - px(16), px(26)),
        f"{index + 1} / {total}",
        font=small_font,
        fill=MUTED,
        anchor="rm",
    )
    y = px(HEADER_H)
    for section in page.sections:
        draw.text(
            (px(16), y + row_h // 2),
            section.title.upper(),
            font=section_font,
            fill=ACCENT,
            anchor="lm",
        )
        y += row_h
        for entry in section.entries:
            draw.rounded_rectangle(
                (px(12), y + px(2), width - px(12), y + row_h - px(2)),
                radius=px(6),
                fill=ROW_BG,
            )
            key_width = min(
                px(148), max(px(52), round(draw.textlength(entry.key, font=small_font)) + px(20))
            )
            draw.rounded_rectangle(
                (px(18), y + px(5), px(18) + key_width, y + row_h - px(5)),
                radius=px(5),
                fill=KEY_BG,
            )
            draw.text(
                (px(18) + key_width // 2, y + row_h // 2),
                entry.key,
                font=small_font,
                fill=WHITE,
                anchor="mm",
            )
            badge = "mpv" if entry.source == "mpv" else entry.context
            badge_width = 0
            if badge:
                badge_width = min(px(124), round(draw.textlength(badge, font=small_font)) + px(16))
                bx = width - badge_width - px(18)
                draw.text(
                    (bx + badge_width // 2, y + row_h // 2),
                    badge,
                    font=small_font,
                    fill=MUTED,
                    anchor="mm",
                )
            text_x = px(18) + key_width + px(12)
            label_width = width - text_x - badge_width - px(28)
            label = _ellipsize(draw, entry.label, body_font, max(px(24), label_width))
            draw.text((text_x, y + row_h // 2), label, font=body_font, fill=WHITE, anchor="lm")
            y += row_h
    draw.line(
        (px(12), height - footer_h, width - px(12), height - footer_h),
        fill=(59, 70, 85, 255),
    )
    draw.text(
        (width // 2, height - footer_h // 2),
        page.footer,
        font=small_font,
        fill=MUTED,
        anchor="mm",
    )
    return image
