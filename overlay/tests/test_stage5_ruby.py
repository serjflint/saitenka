"""Stage 5: isolated ruby — reading centred over base, both narrow and wide cases."""

from util import assert_golden

from overlay.model import Span, Style
from overlay.render.ruby import layout_ruby, render_ruby

BASE = Style(size=44)


def _base(text):
    return [Span(text, BASE)]


def test_reading_centered_narrow():
    # reading narrower than base: 漢字 (base) / かんじ (reading)
    box = layout_ruby(_base("漢字"), "かんじ")
    assert box.reading_width < box.base_width
    _assert_centered(box)


def test_reading_centered_wide():
    # reading wider than base: 兎 (base) / うさぎ (reading)
    box = layout_ruby(_base("兎"), "うさぎ")
    assert box.reading_width > box.base_width
    _assert_centered(box)


def _assert_centered(box):
    x = 0.0
    base_center = box.base_x(x) + box.base_width / 2
    read_center = box.reading_x(x) + box.reading_width / 2
    box_center = x + box.box_width / 2
    assert abs(base_center - box_center) <= 1.0
    assert abs(read_center - box_center) <= 1.0
    # reading sits entirely above the main baseline
    assert box.reading_baseline_dy > 0
    assert box.ascent > box.base_ascent


def test_ruby_narrow_golden():
    assert_golden(
        render_ruby(_base("漢字"), "かんじ", background=(255, 255, 255, 255)), "ruby_narrow.png"
    )


def test_ruby_wide_golden():
    assert_golden(
        render_ruby(_base("兎"), "うさぎ", background=(255, 255, 255, 255)), "ruby_wide.png"
    )
