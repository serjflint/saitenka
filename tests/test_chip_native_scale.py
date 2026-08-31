"""Pills raster natively at the display scale, like the glyphs around them.

The crisp path draws body text through FreeType at ``size×scale`` but used to composite a chip by
LANCZOS-upscaling its 1× sprite, so every pill read soft next to the text it sat beside. The oracles
here are differential (against that upscale, which is the control) and metamorphic (geometry tracks
``scale``), not pixel goldens — antialiasing detail is platform-dependent, the *direction* is not.
"""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image, ImageDraw

from saitenka.render.chip import ChipStyle, render_chip
from saitenka.render.flow import ChipBox, ImgBox, render_chip_row

_CS = ChipStyle(size=16, weight=600, bg=(90, 160, 110, 255), value="264")


def _upscaled(text: str, cs: ChipStyle, scale: float) -> Image.Image:
    """What the chip used to composite: the 1× sprite stretched to the device box."""
    one = render_chip(text, cs).image
    return one.resize(
        (round(one.width * scale), round(one.height * scale)), Image.Resampling.LANCZOS
    )


def _blur_fraction(img: Image.Image) -> float:
    """Share of pixels that are neither ink nor ground. Resampling can only raise it — a stretched
    raster smears every edge into a ramp, a fresh raster only antialiases the true outline."""
    hist = img.convert("L").histogram()
    return sum(hist[24:232]) / sum(hist)


def test_scaled_chip_is_sharper_than_the_upscale_it_replaced():
    native = render_chip("JPDBv2", _CS, scale=2.0).image
    assert _blur_fraction(native) < _blur_fraction(_upscaled("JPDBv2", _CS, 2.0))


def test_scaled_chip_is_not_merely_a_resize_of_the_reference_sprite():
    # Negative control for the test above: without the native path these two are the same pixels.
    native = render_chip("JPDBv2", _CS, scale=2.0).image
    up = _upscaled("JPDBv2", _CS, 2.0)
    assert native.size != up.size or native.tobytes() != up.tobytes()


def test_reference_scale_chip_is_unchanged():
    # 1.0 must stay the byte-identical reference raster — the panel's whole 1× cache depends on it.
    assert (
        render_chip("diff", _CS, scale=1.0).image.tobytes()
        == render_chip("diff", _CS).image.tobytes()
    )


def _pill_gaps(img: Image.Image) -> int:
    """Runs of fully transparent columns between the first and last inked one — the visible gaps."""
    a = img.getchannel("A")
    inked = [any(a.getpixel((x, y)) for y in range(a.height)) for x in range(a.width)]
    if True not in inked:
        return 0
    inner = inked[inked.index(True) : len(inked) - inked[::-1].index(True)]
    return sum(1 for i, v in enumerate(inner) if not v and (i == 0 or inner[i - 1]))


@given(scale=st.floats(min_value=1.05, max_value=3.0))
@settings(max_examples=40, deadline=None)
def test_native_pill_occupies_exactly_the_projected_reference_box(scale):
    """Layout places pills from the 1× metrics projected by ``scale``, so the native raster has to land
    on that box exactly — an integer font size does not scale linearly, and a pill sized from its own
    natural extent came out px wide of its slot."""
    one, native = render_chip("JPDBv2㋕", _CS), render_chip("JPDBv2㋕", _CS, scale=scale)
    assert (native.width, native.height) == (round(one.width * scale), round(one.height * scale))


@given(scale=st.floats(min_value=1.05, max_value=3.0))
@settings(max_examples=30, deadline=None)
def test_native_pills_stay_separated_at_every_scale(scale):
    """What that box buys, seen from outside: a pill that outgrew its slot would eat the gap to its
    neighbour and, far enough, merge two pills into one blob. Holds however the platform's font
    rasteriser rounds — the count of visible gaps is the same as the reference row's."""
    chips = [ChipBox(n, _CS) for n in ("JPDBv2㋕", "Novels", "Jiten")]
    assert _pill_gaps(render_chip_row(chips, 8, 900, scale=scale)) == _pill_gaps(
        render_chip_row(chips, 8, 900, scale=1.0)
    )


@given(scale=st.floats(min_value=1.05, max_value=3.0))
@settings(max_examples=30, deadline=None)
def test_chip_metrics_stay_reference_px_at_every_scale(scale):
    # The scale boundary: only pixels move with the display, never the geometry the hit-test inverts.
    box = ChipBox("Jiten", _CS)
    before = (box.advance, box.ascent, box.descent)
    box.sprite_at(scale)
    assert (box.advance, box.ascent, box.descent) == before


def _counting_native(asked: list[float]):
    def native(s: float) -> Image.Image:
        asked.append(s)
        return Image.new("RGBA", (round(20 * s), round(20 * s)), (0, 200, 0, 255))

    return native


def test_a_box_with_a_native_provider_never_upscales():
    # The seam the pitch graph, the bullet markers and the gaiji share: all three can be drawn at any
    # size, so the box is asked for device pixels rather than handed a stretched 1x sprite.
    asked: list[float] = []
    box = ImgBox(width=20, height=20, native=_counting_native(asked))
    canvas = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    box.draw(canvas, ImageDraw.Draw(canvas), 10, 40, scale=2.0)
    assert asked == [1.0, 2.0]  # 1.0 at construction (the reference sprite), then the device one


def test_the_reference_sprite_is_the_provider_at_scale_one():
    # The dedup that closed #465's gaiji bug: `sprite` is not a second recipe that can disagree with
    # the scaled one, it IS `native(1.0)`.
    asked: list[float] = []
    box = ImgBox(width=20, height=20, native=_counting_native(asked))
    assert asked == [1.0]
    assert box.sprite is not None
    assert box.sprite.size == (20, 20)
    assert box.sprite_at(1.0) is box.sprite  # seeded, so 1.0 is a cache hit, not a recompute
    assert asked == [1.0]


def test_a_box_without_a_provider_still_upscales_its_sprite():
    box = ImgBox(width=20, height=20, sprite=Image.new("RGBA", (20, 20), (0, 200, 0, 255)))
    scaled = box.sprite_at(2.0)
    assert scaled is not None and scaled.size == (40, 40)


def test_the_sprite_memo_is_not_part_of_the_box_s_identity():
    # A cache that joined __eq__/__init__ would make two equal boxes compare unequal once one had
    # rendered, and `replace()` would inherit the other box's pixels.
    a = ImgBox(width=20, height=20, native=_counting_native([]))
    b = replace(a)
    a.sprite_at(2.0)
    assert a == b
    assert b._sprites.keys() == {1.0}


def test_chip_row_raster_is_the_device_size_of_its_reference_row():
    chips = [ChipBox(n, _CS) for n in ("diff", "JLPT", "Jiten")]
    ref = render_chip_row(chips, 8, 400, scale=1.0)
    assert render_chip_row(chips, 8, 400, scale=1.5).size == (
        round(ref.width * 1.5),
        round(ref.height * 1.5),
    )
