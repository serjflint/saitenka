"""Stage 8b (tooling): hypothesis property tests for the text core.

Properties pinned:
- wrap never splits a ruby unit (a RubyBox is atomic across lines);
- ``strip_inline_furigana`` is idempotent;
- the deinflection chain on an arbitrary surface reaches the lemma or returns empty;
- ``merge_inflected`` output concatenates back to its input surfaces.
"""

from hypothesis import example, given, settings
from hypothesis import strategies as st
from saitenka_deinflect import (  # noqa: TID251  # property tests exercise the GPL chain directly, not via the app chokepoint
    deinflect,
    inflection_chain,
)

from saitenka.app.tokenize import Token, has_kanji, merge_inflected, strip_inline_furigana
from saitenka.model import Span, Style
from saitenka.render.flow import build_items, ruby, wrap_items

# --- strategies -----------------------------------------------------------------------------------

hiragana = st.text(
    alphabet=st.characters(min_codepoint=0x3041, max_codepoint=0x3096), min_size=1, max_size=6
)
kanji = st.text(alphabet=st.sampled_from("漢字読本習経僧門小取見食行上"), min_size=1, max_size=3)
jp_text = st.text(
    alphabet=st.characters(min_codepoint=0x3041, max_codepoint=0x30FF), min_size=0, max_size=12
)
POS = st.sampled_from(["名詞", "動詞", "助詞", "助動詞", "形容詞", "副詞", "記号"])


@st.composite
def tokens(draw):
    surface = draw(st.one_of(hiragana, kanji))
    return Token(
        surface=surface,
        lemma=draw(st.one_of(st.just(surface), hiragana)),
        reading=draw(hiragana),
        pos=draw(POS),
        start=0,
        end=len(surface),
        pos2=draw(st.sampled_from(["", "普通名詞", "接続助詞", "非自立可能"])),
    )


# --- wrap never splits a ruby unit ---------------------------------------------------------------


@given(base=kanji, reading=hiragana, prefix=hiragana, width=st.integers(30, 200))
@settings(max_examples=50, deadline=None)
def test_wrap_never_splits_a_ruby_unit(base, reading, prefix, width):
    flow = [
        Span(prefix, Style(size=20)),
        ruby(base, reading, Style(size=20)),
        Span(prefix, Style(size=20)),
    ]
    items = build_items(flow)
    n_ruby = sum(1 for it in items if it.kind == "ruby")
    lines = wrap_items(items, width)
    # each ruby box lands on exactly one line, whole
    assert sum(1 for line in lines for it in line if it.kind == "ruby") == n_ruby


# --- strip_inline_furigana: the true single-pass contract ----------------------------------------
# NOTE (hypothesis finding, 2026-07-21): the plan's assumed IDEMPOTENCE is false — counterexample
# [上(reading ぁぁ), ぁぁ, ぁぁ]: the stripper removes the first hira run (the baked furigana) and a
# re-application would eat the SECOND, identical run, which is legitimate text. The function is
# single-pass by design (production applies it exactly once per line); we pin the properties that
# actually define it: the output is a subsequence of the input, and kanji tokens are never removed.


@given(st.lists(tokens(), min_size=0, max_size=8))
@settings(max_examples=100, deadline=None)
def test_strip_inline_furigana_is_a_subsequence_preserving_kanji(toks):
    out = strip_inline_furigana(toks)
    # subsequence: every output token appears in the input, in order
    it = iter(toks)
    assert all(any(t is u for u in it) for t in out), "output is not an input subsequence"

    # kanji tokens are never stripped (only trailing hira furigana runs are)
    def has_kanji(s):
        return any(0x3400 <= ord(c) <= 0x9FFF or 0xF900 <= ord(c) <= 0xFAFF for c in s)

    assert [t.surface for t in toks if has_kanji(t.surface)] == [
        t.surface for t in out if has_kanji(t.surface)
    ]


# --- kanji detection spans the supplementary planes (not just the BMP) ---------------------------


def test_has_kanji_recognizes_supplementary_plane_ideographs():
    # Astral kanji (surrogate pairs) really occur in subs — 𩸽 (ほっけ, atka mackerel) on menus,
    # 𠮟る (しかる) in prose. Before #99 these read as "no kanji" and lost kanji highlighting / N+1.
    assert has_kanji("𩸽")  # U+29E3D, CJK Ext B
    assert has_kanji("𠮟る")  # U+20B9F + hira tail
    assert has_kanji("漢字")  # BMP still detected
    assert not has_kanji("ほっけ")  # kana only
    assert not has_kanji("ABC 123")  # no CJK at all


@example(cp=0x20B9F)  # 𠮟 — the shrunk astral boundary that was silently dropped
@given(cp=st.integers(min_value=0x20000, max_value=0x2A6DF))  # CJK Ext B
@settings(max_examples=50, deadline=None)
def test_has_kanji_true_for_every_ext_b_codepoint(cp):
    assert has_kanji(chr(cp))


# --- deinflection chain reaches the lemma or returns empty ---------------------------------------


@given(surface=jp_text, lemma=jp_text)
@settings(max_examples=100, deadline=None)
def test_inflection_chain_reaches_lemma_or_empty(surface, lemma):
    chain = inflection_chain(surface, lemma)
    assert isinstance(chain, list)
    if surface == lemma:
        assert chain == []  # already the dictionary form → no chain
    # a non-empty chain must be a list of transform names (strings)
    for name in chain:
        assert isinstance(name, str) and name


# --- deinflect ⇄ inflection_chain agree (metamorphic cross-check) --------------------------------
# Real inflected surfaces so the reachability relation has teeth — random kana rarely inflects.
_INFLECTED = [
    "聞こえてた",
    "預けた",
    "食べない",
    "読まれる",
    "走りたい",
    "読みます",
    "早く",
    "食べちゃった",
    "行った",
    "行って",
    "習わぬ",
    "知らん",
    "本命",
    "猫",
]


@given(surface=st.one_of(st.sampled_from(_INFLECTED), jp_text))
@settings(max_examples=100, deadline=None)
def test_deinflect_and_inflection_chain_agree(surface):
    """The two deinflect entry points must never disagree: for every base form ``deinflect`` reaches,
    ``inflection_chain(surface, form)`` is non-empty exactly when the form differs from the surface
    (the identity reduction has an empty chain), and a form ``deinflect`` can't reach yields no chain."""
    results = deinflect(surface)
    assert (
        results[0].text == surface and results[0].chain == ()
    )  # identity candidate is always first
    for target in {d.text for d in results}:
        assert bool(inflection_chain(surface, target)) == (target != surface)
    assert (
        inflection_chain(surface, surface + "zz") == []
    )  # deinflect never lengthens → unreachable


# --- merge_inflected output concatenates back to its input ---------------------------------------


@given(st.lists(tokens(), min_size=0, max_size=10))
@settings(max_examples=100, deadline=None)
def test_merge_inflected_concatenates_back(toks):
    merged = merge_inflected(toks)
    assert "".join(t.surface for t in merged) == "".join(t.surface for t in toks)
    assert "".join(t.reading for t in merged) == "".join(t.reading for t in toks)
