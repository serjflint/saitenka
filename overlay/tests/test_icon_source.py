"""IconSource seam: font glyph when a vendored font covers it, hand-drawn vector otherwise."""

from __future__ import annotations

import pytest
from PIL import Image
from util import assert_golden

from overlay import fonts
from overlay.draw import icons
from overlay.draw.icon_source import Icon, render_icon


def _opaque(img) -> int:
    return sum(1 for px in img.getdata() if px[3] > 0)


@pytest.mark.parametrize("icon", list(Icon))
def test_every_icon_renders_a_square_sprite_with_content(icon: Icon) -> None:
    sprite = render_icon(icon, 24)
    assert sprite.size == (24, 24)
    assert sprite.mode == "RGBA"
    assert _opaque(sprite) > 0


def test_marker_uses_the_font_glyph_not_the_vector() -> None:
    # ● is covered by Noto Sans JP, so the glyph path must be taken — a different raster than the dot.
    assert render_icon(Icon.MARKER, 24).tobytes() != icons.dot(24, icons.GREEN).tobytes()


def test_speaker_uses_the_vendored_emoji_glyph() -> None:
    # 🔊 comes only from the vendored monochrome Noto Emoji — proves it is wired into the chain.
    assert fonts.font_for_char("\U0001f50a") == "NotoEmoji.ttf"
    assert render_icon(Icon.SPEAKER, 30).tobytes() != icons.speaker(30).tobytes()


def test_marker_falls_back_to_the_vector_when_no_font_covers_the_glyph(monkeypatch) -> None:
    monkeypatch.setattr(fonts, "covers", lambda *_: False)
    assert render_icon(Icon.MARKER, 24).tobytes() == icons.dot(24, icons.GREEN).tobytes()


def test_button_affordances_always_use_the_vector() -> None:
    # ADD/MINED are a filled disc + white glyph — no single-glyph equivalent, so always the vector.
    assert render_icon(Icon.ADD, 22).tobytes() == icons.plus(22, icons.GREEN).tobytes()
    assert render_icon(Icon.MINED, 22).tobytes() == icons.check(22, icons.GREEN).tobytes()


def test_color_override_tints_the_glyph() -> None:
    red = render_icon(Icon.MARKER, 24, (220, 20, 20, 255))
    opaque = [px for px in red.getdata() if px[3] > 0]
    assert opaque and all(px[0] > px[1] and px[0] > px[2] for px in opaque)


def test_icon_sheet_golden() -> None:
    # Pins the actual icon visuals (glyphs + vector affordances) — the behaviour review the panel
    # goldens miss, since each icon is too small a region to move their whole-image MAE.
    size, gap = 40, 8
    order = [Icon.MARKER, Icon.ADD, Icon.MINED, Icon.CLOSE, Icon.SPEAKER]
    sheet = Image.new(
        "RGBA", (len(order) * (size + gap) + gap, size + 2 * gap), (255, 255, 255, 255)
    )
    for i, icon in enumerate(order):
        sheet.alpha_composite(render_icon(icon, size), (gap + i * (size + gap), gap))
    assert_golden(sheet, "icon_sheet.png")
