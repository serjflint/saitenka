"""resvglite smoke tests — the SVG→PNG rasterizer works and refuses bad input.

Run against the built extension: ``uv run --with-editable . pytest -q`` from this dir, or via the CI
wheel job. Kept dependency-light (Pillow only, already a saitenka dep) so it runs anywhere the wheel does.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import resvglite

# A monochrome gaiji-style glyph: one black square on transparent — the common 外字 shape.
_SQUARE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    b'<rect x="10" y="10" width="80" height="80" fill="black"/></svg>'
)
# A wide (2:1) viewBox so the aspect-preserving width math is exercised.
_WIDE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><rect width="200" height="100"/></svg>'

# A <text> gaiji — the 大辞林 漢/呉 badge shape: a font-less render draws only the box (#283 tofu bug).
_BOXED_TEXT = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>"
    b"<rect width='128' height='128' fill='none' stroke='black' stroke-width='8'/>"
    b"<text text-anchor='middle' x='50%' y='50%' dy='.35em' font-family='sans-serif' "
    b"font-size='100' fill='black'>A</text></svg>"
)
# NotoSans (Latin) is enough to prove <text> renders; it's the same OFL face saitenka bundles.
_FONT = (
    Path(__file__).resolve().parents[2] / "overlay/src/overlay/assets/fonts/NotoSans.ttf"
).read_bytes()


def _open(png: bytes) -> Image.Image:
    return Image.open(BytesIO(png))


def _opaque(png: bytes) -> int:
    hist = _open(png).convert("RGBA").getchannel("A").histogram()
    return sum(hist) - hist[0]  # every pixel minus the fully-transparent ones


def test_render_svg_returns_png_at_requested_height():
    png, w, h = resvglite.render_svg(_SQUARE, 64)
    assert h == 64 and w == 64  # square viewBox → square raster
    img = _open(png)
    assert img.format == "PNG" and img.mode == "RGBA" and img.size == (64, 64)


def test_width_tracks_aspect_ratio():
    _png, w, h = resvglite.render_svg(_WIDE, 40)
    assert h == 40 and w == 80  # 2:1 viewBox at 40px tall → 80px wide


def test_transparent_background_and_real_ink():
    png, _w, _h = resvglite.render_svg(_SQUARE, 64)
    alpha = _open(png).getchannel("A")
    assert alpha.getextrema() == (0, 255)  # both fully-transparent margin and fully-opaque glyph


def test_boxed_text_renders_the_glyph_when_a_font_is_provided():
    # The #283 regression guard: 漢/呉-style <text> gaiji must show ink beyond the bare box.
    box_only = _opaque(resvglite.render_svg(_BOXED_TEXT, 64)[0])
    with_glyph = _opaque(resvglite.render_svg(_BOXED_TEXT, 64, [_FONT])[0])
    assert box_only > 0  # the border always rasterizes
    assert with_glyph > box_only  # the glyph adds ink — a real character, not an empty ▢


def test_text_is_dropped_without_a_font():
    # No font → the <text> glyph vanishes; only a stroked box would remain (here: none, no <rect>).
    text_svg = (
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>"
        b"<text x='10' y='90' font-family='sans-serif' font-size='100' fill='black'>A</text></svg>"
    )
    assert _opaque(resvglite.render_svg(text_svg, 64)[0]) == 0
    assert _opaque(resvglite.render_svg(text_svg, 64, [_FONT])[0]) > 0


def test_invalid_svg_raises_value_error():
    with pytest.raises(ValueError):
        resvglite.render_svg(b"not an svg", 64)


def test_zero_size_raises_value_error():
    with pytest.raises(ValueError):
        resvglite.render_svg(_SQUARE, 0)
