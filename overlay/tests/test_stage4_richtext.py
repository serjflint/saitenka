"""Stage 4: styled runs — bold / colour / size mixed within one wrapped paragraph."""

from overlay.model import Span, Style
from overlay.render.layout import Block, render_rich
from util import assert_golden

RED = (200, 40, 40, 255)
GREY = (120, 120, 120, 255)

RICH = [
    Span("読む", Style(size=30, weight=700, color=RED)),
    Span(" は ", Style(size=24)),
    Span("文字を声に出して", Style(size=24, weight=700)),
    Span("読むこと。", Style(size=24)),
    Span("  ← smaller grey note", Style(size=18, color=GREY)),
]


def test_richtext_golden():
    img = render_rich(RICH, Block(width=300, background=(255, 255, 255, 255)))
    assert_golden(img, "richtext.png")


def test_italic_differs_from_upright():
    up = render_rich([Span("kana", Style(size=40))], Block(width=200))
    it = render_rich([Span("kana", Style(size=40, italic=True))], Block(width=200))
    # same canvas size, but the sheared render must differ from upright
    assert up.size == it.size
    import numpy as np

    assert np.abs(np.asarray(up, np.int16) - np.asarray(it, np.int16)).mean() > 1.0
