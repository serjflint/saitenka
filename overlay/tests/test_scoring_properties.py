"""Property tests for the scoring core — the SubMiner colour-priority model and the N+1 selector.

Invariants that pin the branch structure of `Scorer._style` and `mark_n_plus_one` so a mutation
(flipping a priority test, dropping an enable-flag guard, weakening the N+1 eligibility filter) turns
one of these red. Synthetic tokens (no tokenizer) keep it fast and deterministic. Companion to the
example-based test_coloring.py; drives `uv run poe mutate scoring`.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given

from overlay.app.scoring import FUNCTION_POS, Palette, Scorer, mark_n_plus_one
from overlay.app.tokenize import Token
from overlay.app.wordlists import FreqDict, JlptDict, KnownWords

PAL = Palette()
CONTENT_POS = ["名詞", "動詞", "形容詞", "副詞"]
FUNC_POS = sorted(FUNCTION_POS)
_ALPHA = "あいうえおかきくけこ本読私新語人待犬"  # kana + kanji so is_kana_only varies


def _content(t: Token) -> bool:
    """Oracle mirroring scoring._is_content, kept independent of the private helper under test."""
    return bool(t.surface.strip()) and t.pos not in FUNCTION_POS


@st.composite
def _token(draw) -> Token:
    pos = draw(st.sampled_from(CONTENT_POS + FUNC_POS))
    surface = draw(st.text(alphabet=_ALPHA, min_size=1, max_size=3))
    reading = draw(st.text(alphabet="あいうえおかきくけこ", min_size=1, max_size=3))
    pos2 = draw(st.sampled_from(["", "固有名詞"])) if pos == "名詞" else ""
    return Token(surface, surface, reading, pos, 0, len(surface), pos2)


@st.composite
def _line(draw) -> tuple[list[Token], list[bool]]:
    toks = draw(st.lists(_token(), max_size=8))
    known = draw(st.lists(st.booleans(), min_size=len(toks), max_size=len(toks)))
    return toks, known


def _all_forms(toks: list[Token]) -> list[str]:
    return [f for t in toks for f in (t.lemma, t.surface, t.reading)]


# --- mark_n_plus_one ------------------------------------------------------------------------------


@given(_line(), st.integers(min_value=1, max_value=4))
def test_n1_targets_are_always_eligible(line, min_words):
    """Every marked index is an unknown, non-kana-only, non-proper content word — the eligibility
    filter that decides which single word gets the N+1 highlight."""
    toks, known = line
    for j in mark_n_plus_one(toks, known, min_words):
        assert _content(toks[j])
        assert not known[j]
        assert not toks[j].is_kana_only
        assert not toks[j].is_proper_noun


@given(_line())
def test_n1_needs_a_genuinely_single_candidate(line):
    """A sentence with no boundary punctuation is one sentence; it may mark at most one word, and
    only when there's exactly one eligible candidate among ≥min_words content words."""
    toks, known = line
    if any(c in "。？！?!…" for t in toks for c in t.surface):
        return  # multi-sentence lines are covered by the eligibility invariant above
    content = [j for j, t in enumerate(toks) if _content(t)]
    eligible = [
        j
        for j in content
        if not known[j] and not toks[j].is_kana_only and not toks[j].is_proper_noun
    ]
    targets = mark_n_plus_one(toks, known, min_words=3)
    if len(content) >= 3 and len(eligible) == 1:
        assert targets == {eligible[0]}
    else:
        assert targets == set()


# --- Scorer.score_line / _style priority ----------------------------------------------------------


@given(_line())
def test_score_line_is_total_and_deterministic(line):
    toks, _ = line
    sc = Scorer(known=KnownWords.from_set([]))
    styles = sc.score_line(toks)
    assert len(styles) == len(toks)
    assert styles == sc.score_line(toks)  # pure — same input, same styles


@given(_line())
def test_function_words_stay_base_even_with_jlpt_and_freq(line):
    """Non-content (particle/aux/symbol) tokens never take a colour or underline, no matter what the
    JLPT/freq dicts say — the content-word gate in _style."""
    toks, known = line
    jl = JlptDict(dict.fromkeys(_all_forms(toks), "N3"))
    fq = FreqDict(dict.fromkeys(_all_forms(toks), 5), "t")
    sc = Scorer(
        known=KnownWords.from_set([t.surface for t, k in zip(toks, known, strict=True) if k]),
        jlpt=jl,
        freq=fq,
    )
    for t, s in zip(toks, sc.score_line(toks), strict=True):
        if not _content(t):
            assert s.color == PAL.base
            assert s.underline is None
            assert "jlpt" not in s.tag


@given(_line())
def test_n1_wins_over_everything(line):
    """A word selected as N+1 always gets the N+1 colour/tag — top of the priority order."""
    toks, _ = line
    sc = Scorer(known=KnownWords.from_set([]), enable_freq=False, enable_jlpt=False)
    n1 = mark_n_plus_one(toks, [False] * len(toks), sc.min_sentence_words)
    styles = sc.score_line(toks)
    for i in n1:
        assert styles[i].color == PAL.n_plus_one
        assert styles[i].tag.startswith("n+1")


@given(_line())
def test_all_known_content_gets_known_colour(line):
    """When every word is known, N+1 can't fire (it needs an unknown), so each content word takes the
    known colour — the known branch below N+1."""
    toks, _ = line
    sc = Scorer(
        known=KnownWords.from_set([t.surface for t in toks]), enable_freq=False, enable_jlpt=False
    )
    for t, s in zip(toks, sc.score_line(toks), strict=True):
        if _content(t):
            assert s.color == PAL.known


@given(_line())
def test_disable_known_suppresses_known_colour(line):
    """enable_known=False must stop the known colour appearing even when the word is in KnownWords."""
    toks, _ = line
    sc = Scorer(
        known=KnownWords.from_set([t.surface for t in toks]),
        enable_known=False,
        enable_freq=False,
        enable_jlpt=False,
        enable_n_plus_one=False,
    )
    for s in sc.score_line(toks):
        assert s.color != PAL.known  # everything falls through to base


@given(_line())
def test_jlpt_level_suppresses_frequency(line):
    """SubMiner rule: frequency colours only a word with NO other signal — a JLPT level (an additive
    underline) suppresses the freq colour. So content words keep base colour + JLPT underline, never
    a freq tag."""
    toks, _ = line
    jl = JlptDict(dict.fromkeys(_all_forms(toks), "N3"))
    fq = FreqDict(dict.fromkeys(_all_forms(toks), 5), "t")
    sc = Scorer(known=KnownWords.from_set([]), jlpt=jl, freq=fq, enable_n_plus_one=False)
    for t, s in zip(toks, sc.score_line(toks), strict=True):
        if _content(t):
            assert s.underline == PAL.jlpt["N3"]
            assert not s.tag.startswith("freq")
