"""Stage 5: isolated ruby — reading centred over base, both narrow and wide cases."""

import pytest
from util import assert_golden

from saitenka.model import Span, Style
from saitenka.render.ruby import layout_ruby, render_ruby

BASE = Style(size=44)


def _base(text):
    return [Span(text, BASE)]


@pytest.mark.parametrize(
    ("base", "reading", "width_direction"),
    [("漢字", "かんじ", -1), ("兎", "うさぎ", 1)],
    ids=["narrow-reading", "wide-reading"],
)
def test_ruby_reading_is_centered(base, reading, width_direction):
    box = layout_ruby(_base(base), reading)
    assert (box.reading_width - box.base_width) * width_direction > 0
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
