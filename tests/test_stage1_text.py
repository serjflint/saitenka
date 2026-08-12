"""Stage 1: plain shaped text → PNG."""

from util import assert_golden

from saitenka.render.text import TextOpts, rasterize


def test_plain_text_golden():
    img = rasterize("Saitenka", TextOpts(size=48))
    assert img.mode == "RGBA"
    # not blank: some pixels have alpha
    assert img.getextrema()[3][1] > 0
    assert_golden(img, "plain.png")
