"""resvglite smoke tests — the SVG→PNG rasterizer works and refuses bad input.

Run against the built extension: ``uv run --with-editable . pytest -q`` from this dir, or via the CI
wheel job. Kept dependency-light (Pillow only, already a saitenka dep) so it runs anywhere the wheel does.
"""

from __future__ import annotations

from io import BytesIO

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


def _open(png: bytes) -> Image.Image:
    return Image.open(BytesIO(png))


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


def test_invalid_svg_raises_value_error():
    with pytest.raises(ValueError):
        resvglite.render_svg(b"not an svg", 64)


def test_zero_size_raises_value_error():
    with pytest.raises(ValueError):
        resvglite.render_svg(_SQUARE, 0)
