"""MVP: subtitle rendering (multi-line) + per-word hitbox geometry."""

from overlay.app.subtitles import render_subtitle
from overlay.app.tokenize import tokenize
from util import assert_golden

LINE = "門前の小僧習わぬ経を読む"


def test_hitboxes_cover_every_token_in_order():
    toks = tokenize(LINE)
    sr = render_subtitle([toks], osd_w=1280, size=44)
    assert len(sr.boxes) == len(toks)
    assert [b.index for b in sr.boxes] == list(range(len(toks)))
    xs = [b.x for b in sr.boxes]
    assert xs == sorted(xs)  # left-to-right on one line
    for a, b in zip(
        sr.boxes, sr.boxes[1:], strict=False
    ):  # adjacent pairs — lengths differ by design     # adjacent, non-overlapping
        assert a.x + a.w <= b.x + 1


def test_box_contains_hit():
    toks = tokenize(LINE)
    sr = render_subtitle([toks], osd_w=1280, size=44)
    b = sr.boxes[-1]  # 読む (last token; 習わ+ぬ merged so indices shift)
    assert b.contains(b.x + b.w / 2, b.y + b.h / 2)
    assert not b.contains(b.x - 5, b.y + b.h / 2)


def test_shrinks_to_fit_width():
    toks = tokenize(LINE * 4)  # a single very long token stream
    sr = render_subtitle([toks], osd_w=1280, size=44)
    assert sr.image.width <= 1280


def test_explicit_line_breaks_stack_vertically():
    l1, l2 = tokenize("私は本を読む"), tokenize("門前の小僧習わぬ経を読む")
    sr = render_subtitle([l1, l2], osd_w=1280, size=44)
    # global indices are row-major and contiguous
    assert [b.index for b in sr.boxes] == list(range(len(l1) + len(l2)))
    ys = sorted({b.y for b in sr.boxes})
    assert len(ys) == 2, "two source lines → two distinct box rows"
    # last token of line 1 sits on the top row, first token of line 2 on the bottom row
    assert sr.boxes[len(l1) - 1].y == ys[0]
    assert sr.boxes[len(l1)].y == ys[1]


def test_long_line_wraps_to_multiple_rows():
    toks = tokenize(LINE * 3)
    sr = render_subtitle([toks], osd_w=900, size=44)
    assert len({b.y for b in sr.boxes}) >= 2  # wrapped onto ≥2 visual rows
    assert sr.image.width <= 900


def test_subtitle_golden_with_hover():
    toks = tokenize(LINE)
    sr = render_subtitle([toks], osd_w=1280, size=44, hover=7)
    assert_golden(sr.image, "subtitle_yomu.png")
