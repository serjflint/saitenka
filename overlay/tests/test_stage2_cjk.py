"""Stage 2: mixed JP/EN shapes with font fallback and no missing glyphs (tofu)."""

from overlay import fonts
from overlay.render.text import TextOpts, rasterize
from util import assert_golden

MIXED = "これは test 日本語。"


def test_no_missing_glyphs():
    assert fonts.missing_glyphs(MIXED) == []


def test_fallback_runs_cover_everything():
    # Every run maps to a real vendored file, and concatenating runs rebuilds the string.
    runs = fonts.resolve_runs(MIXED)
    assert "".join(r.text for r in runs) == MIXED
    for r in runs:
        assert r.file in fonts.FONT_FILES


def test_cjk_mixed_golden():
    img = rasterize(MIXED, TextOpts(size=40))
    assert img.getextrema()[3][1] > 0
    assert_golden(img, "cjk_mixed.png")
