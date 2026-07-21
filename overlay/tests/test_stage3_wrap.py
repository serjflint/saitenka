"""Stage 3: line wrapping + block width."""

from overlay.render.layout import NO_START, Block, render_paragraph, tokenize, wrap
from overlay.render.text import TextOpts
from util import assert_golden

# A real dictionary definition line (from the 読む entry) — long enough to wrap.
PARA = "文字で書かれている文や文章を一字ずつ声に出して言う。"


def test_wraps_to_expected_line_count():
    opts = TextOpts(size=24)
    lines = wrap(tokenize(PARA, opts), max_width=300)
    assert len(lines) >= 2
    # every character is preserved across the wrap
    joined = "".join(t.text for line in lines for t in line)
    assert joined == PARA


def test_kinsoku_no_leading_closing_punct():
    opts = TextOpts(size=24)
    for w in range(160, 340, 7):
        lines = wrap(tokenize(PARA, opts), max_width=w)
        for line in lines[1:]:  # a wrapped line must not start with a NO_START char
            assert line[0].text not in NO_START, (w, line[0].text)


def test_wrap_golden():
    img = render_paragraph(PARA, Block(width=300), TextOpts(size=24))
    assert_golden(img, "wrap.png")
