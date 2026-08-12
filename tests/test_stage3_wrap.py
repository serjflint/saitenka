"""Stage 3: line wrapping + block width."""

from util import assert_golden

from saitenka.render.layout import NO_START, Block, render_paragraph, tokenize, wrap
from saitenka.render.text import TextOpts

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


def test_latin_word_run_stays_whole_but_punctuation_splits_off():
    # #149 atlas-saturation: a letter run is one token (real word → the atlas keys on vocabulary),
    # while adjacent digits/punctuation each become their own token so "cat,"/"[1]" stop minting
    # unique atlas runs. Combining marks stay inside the word (a decomposed diacritic must not split it).
    toks = [t.text for t in tokenize("cat, [1] Śakrá", TextOpts(size=16)) if t.kind != "space"]
    assert toks == ["cat", ",", "[", "1", "]", "Śakrá"]


def test_splitting_punctuation_preserves_total_width():
    # Pillow has no libraqm here (pure advance layout), so splitting a run into per-glyph tokens is
    # byte-identical in width — the pen lands each glyph at the same x, so pixels don't move. A
    # space-free run's tokenized widths must sum to the single-run advance the old tokenizer used.
    from saitenka import fonts

    opts = TextOpts(size=16)
    font = fonts.load(fonts.FontSpec(fonts.FONT_FILES[0], 16, 400))
    for s in ("word.", "well-known", "[42]"):
        split_sum = sum(t.width for t in tokenize(s, opts))
        assert split_sum == fonts.text_width(font, s)
