"""WP5: the translation box's geometry, checkable at any screen size without a session."""

from __future__ import annotations

import pytest

from saitenka.app.translation import clean_secondary, render_translation

HD = (1920, 1080)
RETINA = (3024, 1898)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("one\\Ntwo", "one two"),  # ASS line break
        ("one\ntwo", "one two"),
        ("  padded  ", "padded"),
        (None, ""),  # mpv answers None before a secondary track resolves
        ("", ""),
    ],
)
def test_secondary_text_becomes_one_line(raw: object, expected: str) -> None:
    assert clean_secondary(raw) == expected


def test_the_box_is_centred_horizontally() -> None:
    _image, x, _y = render_translation("I want you to read this.", HD)
    image, _x, _ = render_translation("I want you to read this.", HD)

    assert x == (HD[0] - image.width) // 2


def test_the_box_sits_near_the_top_clear_of_the_subtitle() -> None:
    """Top of the screen, SubMiner-style: the JP subs are at the bottom and the tooltip anchors
    above the hovered word, so a translation drawn low would land on one or the other."""
    _image, _x, y = render_translation("hello", HD)

    assert 0 < y < HD[1] // 4


def test_a_short_line_gets_a_short_box() -> None:
    """It wraps only past 80% of the width, so the box tracks the text rather than the screen."""
    short, _x, _y = render_translation("hi", HD)

    assert short.width < HD[0] * 0.8


def test_a_long_line_is_capped_near_the_wrap_width() -> None:
    """Pins today's arithmetic, including a discrepancy worth knowing about: the cap is applied to
    the *block* width and the padding is then added outside it, so the image runs one padding wider
    than the 80% it reads as. Behaviour preserved through the reader-parameter split rather than
    corrected in passing — every translation box would move by 28 px.
    """
    long_text = "a very long english translation line " * 8

    image, x, _y = render_translation(long_text, HD)

    assert image.width <= int(HD[0] * 0.8) + 2 * 14
    assert x >= 0  # still on screen


def test_the_geometry_scales_with_the_screen() -> None:
    """Every number in the layout is a fraction of the OSD, and the regression that motivates
    checking it is chrome silently staying at 1080p geometry on a Retina panel."""
    _hd_image, _hd_x, hd_y = render_translation("hello", HD)
    retina_image, _r_x, retina_y = render_translation("hello", RETINA)
    hd_image, _, _ = render_translation("hello", HD)

    assert retina_y > hd_y
    assert retina_image.height > hd_image.height  # larger type, not just a larger canvas
