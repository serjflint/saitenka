"""Icon sprites: font glyph first, hand-drawn vector fallback.

``render_icon(Icon.X, size, color)`` renders a monochrome glyph tinted ``color`` — CJK symbols come
from Noto Sans JP, emoji/pictographs from the vendored monochrome Noto Emoji, both via the normal
font-coverage chain (``fonts.FONT_FILES``). When no vendored font covers the glyph it falls back to the
hand-drawn vector in ``draw/icons.py``. The seam is why adding an icon is now "pick a codepoint" rather
than "draw arcs by hand" — the churn that produced the placeholder dot in ``icons.py``.
"""

from __future__ import annotations

from enum import Enum

from PIL import Image, ImageDraw

from saitenka import fonts
from saitenka.draw import icons

RGBA = tuple[int, int, int, int]


class Icon(Enum):
    MARKER = "marker"  # bullet before grammar tags / the inflection chain
    ADD = "add"  # add-to-Anki button
    MINED = "mined"  # add button, already-in-deck state
    CLOSE = "close"  # preview close button
    SPEAKER = "speaker"  # TTS button


# Glyph codepoint per icon (None → always vector). Coverage decides glyph-vs-vector per call, so an
# icon whose glyph a font lacks degrades to its vector automatically.
_GLYPH: dict[Icon, str | None] = {
    Icon.MARKER: "●",  # ● BLACK CIRCLE — faithful to the filled dot (Noto Sans JP)
    Icon.CLOSE: "×",  # × MULTIPLICATION SIGN (Noto Sans JP)
    Icon.SPEAKER: "\U0001f50a",  # 🔊 SPEAKER WITH SOUND WAVES (monochrome Noto Emoji)
    Icon.ADD: None,  # filled disc + white glyph is a button affordance → keep the vector
    Icon.MINED: None,
}

# Em-scale so the glyph fills the box roughly like the vector it replaces (tuned against goldens).
_SCALE: dict[Icon, float] = {Icon.MARKER: 1.15, Icon.CLOSE: 1.0, Icon.SPEAKER: 1.0}

_DEFAULT_COLOR: dict[Icon, RGBA] = {
    Icon.MARKER: icons.GREEN,
    Icon.ADD: icons.GREEN,
    Icon.MINED: icons.GREEN,
    Icon.CLOSE: (150, 150, 150, 255),
    Icon.SPEAKER: icons.SPEAKER,
}

_VECTOR = {
    Icon.MARKER: icons.dot,
    Icon.ADD: icons.plus,
    Icon.MINED: icons.check,
    Icon.CLOSE: icons.cross,
    Icon.SPEAKER: icons.speaker,
}


def _glyph_file(ch: str) -> str | None:
    """First vendored font covering ``ch``, or None (→ caller uses the vector fallback)."""
    for f in fonts.FONT_FILES:
        if fonts.covers(f, ch):
            return f
    return None


def _render_glyph(ch: str, file: str, size: int, color: RGBA, scale: float) -> Image.Image:
    """A single glyph, tinted ``color``, centred in a ``size``×``size`` transparent box."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    font = fonts.load(fonts.FontSpec(file, max(1, round(size * scale))))
    left, top, right, bottom = font.getbbox(ch)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom - top)) / 2 - top
    ImageDraw.Draw(img).text((x, y), ch, font=font, fill=color)
    return img


def render_icon(icon: Icon, size: int, color: RGBA | None = None) -> Image.Image:
    """A ``size``×``size`` RGBA icon sprite — a font glyph when a vendored font covers it, else the
    hand-drawn vector. ``color`` overrides the icon's default tint."""
    col = color if color is not None else _DEFAULT_COLOR[icon]
    ch = _GLYPH.get(icon)
    if ch is not None:
        file = _glyph_file(ch)
        if file is not None:
            return _render_glyph(ch, file, size, col, _SCALE.get(icon, 1.0))
    return _VECTOR[icon](size, col)
