"""Pillow renderer for the static episode-analysis saitenka."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from saitenka import fonts

if TYPE_CHECKING:
    from saitenka.app.features.analysis.episode_analysis import EpisodeAnalysis

BG = (13, 18, 26, 248)
ROW_BG = (25, 33, 45, 235)
WHITE = (244, 247, 251, 255)
MUTED = (157, 171, 190, 255)
ACCENT = (113, 190, 255, 255)


def _font(size: int, weight: int = 400):
    return fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], size, weight))


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _distribution(value) -> str:
    compact = {"Unranked": "other", **{f"Band {i}": f"B{i}" for i in range(1, 6)}}
    return " · ".join(f"{compact.get(label, label)} {count}" for label, count in value if count)


def _rows(result: EpisodeAnalysis | None, status: str) -> tuple[tuple[str, str], ...]:
    if result is None:
        return ((status, ""),)
    return (
        ("Sentences / content tokens", f"{result.sentence_count} / {result.content_token_count}"),
        ("Unique lemmas / kanji", f"{len(result.unique_lemmas)} / {len(result.unique_kanji)}"),
        ("Unique unknown lemmas", str(len(result.unknown_lemmas))),
        ("Known token coverage", _percent(result.known_token_coverage)),
        ("Known type coverage", _percent(result.known_type_coverage)),
        ("N+1 / N+2 sentences", f"{result.n_plus_one_count} / {result.n_plus_two_count}"),
        (
            "JLPT",
            _distribution(result.jlpt_distribution)
            if result.jlpt_distribution is not None
            else "source unavailable",
        ),
        (
            "Frequency",
            _distribution(result.frequency_distribution)
            if result.frequency_distribution is not None
            else "source unavailable",
        ),
    )


def render_analysis(
    result: EpisodeAnalysis | None,
    status: str,
    *,
    osd: tuple[int, int],
    close_key: str,
    scale: float = 1.0,
) -> Image.Image:
    def px(value: int) -> int:
        return max(1, round(value * scale))

    width = max(px(320), min(px(680), osd[0] - px(32)))
    rows = _rows(result, status)
    height = px(78) + len(rows) * px(40) + px(38)
    image = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(image)
    title = _font(px(22), 650)
    body = _font(px(15))
    small = _font(px(12))
    draw.text((px(18), px(28)), "Episode analysis", font=title, fill=WHITE, anchor="lm")
    draw.text(
        (width - px(18), px(28)),
        f"{close_key} close",
        font=small,
        fill=MUTED,
        anchor="rm",
    )
    y = px(58)
    for label, value in rows:
        draw.rounded_rectangle((px(12), y, width - px(12), y + px(34)), radius=px(6), fill=ROW_BG)
        draw.text((px(22), y + px(17)), label, font=body, fill=ACCENT, anchor="lm")
        if value:
            draw.text((width - px(22), y + px(17)), value, font=body, fill=WHITE, anchor="rm")
        y += px(40)
    draw.text(
        (width // 2, height - px(18)),
        "Static subtitle-track metrics · playback unchanged",
        font=small,
        fill=MUTED,
        anchor="mm",
    )
    return image
