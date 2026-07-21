"""Stage 1: plain shaped text → PNG."""

from overlay.render.text import TextOpts, rasterize
from util import assert_golden


def test_plain_text_golden():
    img = rasterize("Saitenka", TextOpts(size=48))
    assert img.mode == "RGBA"
    # not blank: some pixels have alpha
    assert img.getextrema()[3][1] > 0
    assert_golden(img, "plain.png")
