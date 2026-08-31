"""Every inline drawable stays inside the box it reserved, at every display scale.

This is the invariant the gaiji overdraw broke and no test caught (#465): the sprite was composited
at its stored 64px while the box reserved 24px, so it painted over the text beside it. Nothing was
wrong with the pixels, the size, or the tint — each of which *was* asserted. What was missing was the
relation between what a drawable *reserves* and what it *paints*.

Layout places every item from its reference-px metrics and projects them by the scale, so that
relation has to hold at 1.0 and above alike. It is asserted here against the drawn canvas rather than
against a returned size, because the bug lived in the compositing step, past every size a caller
could inspect.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image, ImageDraw

from saitenka.draw.icon_source import Icon, render_icon
from saitenka.draw.pitch import render_pitch_graph
from saitenka.render.chip import ChipStyle
from saitenka.render.flow import ChipBox, ImgBox, img_box

_ORIGIN = 40  # px of slack on every side, so an overdraw has somewhere to show up


def _gaiji_png(side: int = 64) -> bytes:
    """A gaiji as `dictdb` stores one: rasterised at a fixed base height, far above its drawn size."""
    from io import BytesIO

    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    img.paste((0, 0, 0, 255), (side // 4, side // 4, side * 3 // 4, side * 3 // 4))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _drawables():
    """One of every inline drawable that paints a sprite, with the box each one reserves."""
    return {
        "gaiji": img_box(_gaiji_png(), 24, None),
        "pitch-graph": ImgBox(
            width=(g := render_pitch_graph("すごい", 0)).width,
            height=g.height,
            native=lambda s: render_pitch_graph("すごい", 0, scale=s),
        ),
        "marker": ImgBox(
            width=18, height=18, native=lambda s: render_icon(Icon.MARKER, max(1, round(18 * s)))
        ),
        "pill": ChipBox("Novels", ChipStyle(size=16, weight=600, bg=(90, 160, 110, 255))),
        "two-tone-pill": ChipBox(
            "JPDBv2㋕", ChipStyle(size=16, weight=600, bg=(90, 160, 110, 255), value="481")
        ),
    }


def _painted(box, scale: float) -> tuple[int, int]:
    """The bounding box of what ``box`` actually paints, in device px.

    ``draw`` takes REFERENCE-px coordinates and projects them itself, so the canvas is sized in device
    px from the same projection — with slack on every side, or an overdraw would be clipped by the
    edge instead of caught."""
    x, baseline = _ORIGIN, _ORIGIN * 2
    canvas = Image.new(
        "RGBA",
        (
            max(1, round((x + box.advance + _ORIGIN) * scale)),
            max(1, round((baseline + box.descent + _ORIGIN) * scale)),
        ),
        (0, 0, 0, 0),
    )
    box.draw(canvas, ImageDraw.Draw(canvas), x, baseline, scale=scale)
    bbox = canvas.getbbox()
    assert bbox is not None, "the drawable painted nothing at all"
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


@pytest.mark.parametrize("name", sorted(_drawables()))
@given(scale=st.floats(min_value=1.0, max_value=3.0))
@settings(max_examples=20, deadline=None)
def test_a_drawable_never_paints_outside_the_box_it_reserved(name, scale):
    box = _drawables()[name]
    reserved = (round(box.advance * scale), round((box.ascent + box.descent) * scale))
    painted_w, painted_h = _painted(box, scale)
    # Smaller is fine — a glyph rarely fills its em box, and antialiasing trims the edge. Larger is
    # the defect: it lands on whatever the layout put next to this item.
    assert painted_w <= reserved[0] + 1
    assert painted_h <= reserved[1] + 1


@pytest.mark.parametrize("name", sorted(_drawables()))
def test_a_drawable_fills_the_box_it_reserved_at_the_reference_scale(name):
    # The other half: a drawable that reserves far more than it paints wastes the line and would let
    # a collapsed raster pass the bound above. 1.0 is called out on its own because it is the scale
    # the panel cache is built at, and the one the gaiji bug was confined to.
    box = _drawables()[name]
    reserved = (round(box.advance), round(box.ascent + box.descent))
    painted_w, painted_h = _painted(box, 1.0)
    assert painted_w >= reserved[0] * 0.5
    assert painted_h >= reserved[1] * 0.5
