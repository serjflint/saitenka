"""Stage 6: ruby in flow — inline ruby inside wrapped rich text."""

from overlay.model import Span, Style
from overlay.render.flow import build_items, render_flow, ruby, wrap_items
from overlay.render.layout import Block
from util import assert_golden

S = Style(size=30)


def _flow():
    # 門前の小僧習わぬ経を読む — the proverb from the 読む entry, ruby on each kanji group.
    return [
        ruby("門前", "もんぜん", S),
        Span("の", S),
        ruby("小僧", "こぞう", S),
        ruby("習", "なら", S),
        Span("わぬ", S),
        ruby("経", "きょう", S),
        Span("を", S),
        ruby("読", "よ", S),
        Span("む", S),
    ]


def test_flow_wraps_two_lines():
    lines = wrap_items(build_items(_flow()), max_width=220)
    assert len(lines) >= 2


def test_wrap_never_splits_a_ruby_group():
    # A ruby item is atomic: it is never split, and appears exactly once per original ruby.
    items = build_items(_flow())
    ruby_count = sum(1 for it in items if it.kind == "ruby")
    for w in range(120, 360, 9):
        lines = wrap_items(items, max_width=w)
        seen = sum(1 for line in lines for it in line if it.kind == "ruby")
        assert seen == ruby_count


def test_ruby_flow_golden():
    img = render_flow(_flow(), Block(width=220, background=(255, 255, 255, 255)))
    assert_golden(img, "ruby_flow.png")
