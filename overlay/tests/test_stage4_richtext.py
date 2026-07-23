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


def test_ruby_base_kanji_is_scannable():
    # Regression: a kanji rendered with furigana (a ruby box) used to be skipped by the scan pass, so
    # it couldn't be hovered inside the tooltip. Its base kanji must now emit ScanBoxes, and continue
    # the contiguous run so a word spanning ruby + okurigana (習わ) scans as one.
    from overlay.render.flow import render_flow, ruby

    st = Style(size=24)
    flow = [ruby("習", "なら", st), Span("わ", st), Span("ない", st), ruby("経", "きょう", st)]
    scan: list = []
    render_flow(flow, Block(width=400, padding=4, background=(0, 0, 0, 0)), scan_out=scan)
    tails = [sb.text for sb in scan]
    assert (
        "習わない経" in tails
    )  # the ruby'd 習 is scannable AND carries the full de-inflectable tail
    assert any(sb.text == "経" for sb in scan)  # the second ruby'd kanji is scannable too
    xs = [sb.x for sb in scan]
    assert xs == sorted(
        xs
    )  # boxes laid out left→right (ruby base placed at its on-screen position)


def test_dot_marker_is_a_crisp_medium_circle():
    # The grammar/deinflection marker is a medium green dot (a tiny puzzle piece was unrecognisable).
    # Supersampled → crisp (alpha spans 0..255), centred, round (transparent corners), ~2/3 of the box.
    import numpy as np

    from overlay.draw.icons import dot

    a = np.asarray(dot(40))[:, :, 3]  # alpha channel
    assert a.max() == 255 and a.min() == 0  # crisp filled circle on transparent
    assert a[20, 20] == 255  # centre solid
    assert a[1, 1] == 0 and a[38, 38] == 0  # corners transparent → round, not a square blob
    filled = int((a > 128).sum())
    assert 0.25 < filled / a.size < 0.45  # medium — a dot, not filling the box
