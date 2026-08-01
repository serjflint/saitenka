"""Pillow renderer for the static episode-analysis overlay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from overlay import fonts

if TYPE_CHECKING:
    from overlay.app.episode_analysis import EpisodeAnalysis

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
    result: EpisodeAnalysis | None, status: str, *, osd: tuple[int, int], close_key: str
) -> Image.Image:
    width = max(320, min(680, osd[0] - 32))
    rows = _rows(result, status)
    height = 78 + len(rows) * 40 + 38
    image = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(image)
    title = _font(22, 650)
    body = _font(15)
    small = _font(12)
    draw.text((18, 28), "Episode analysis", font=title, fill=WHITE, anchor="lm")
    draw.text((width - 18, 28), f"{close_key} close", font=small, fill=MUTED, anchor="rm")
    y = 58
    for label, value in rows:
        draw.rounded_rectangle((12, y, width - 12, y + 34), radius=6, fill=ROW_BG)
        draw.text((22, y + 17), label, font=body, fill=ACCENT, anchor="lm")
        if value:
            draw.text((width - 22, y + 17), value, font=body, fill=WHITE, anchor="rm")
        y += 40
    draw.text(
        (width // 2, height - 18),
        "Static subtitle-track metrics · playback unchanged",
        font=small,
        fill=MUTED,
        anchor="mm",
    )
    return image
