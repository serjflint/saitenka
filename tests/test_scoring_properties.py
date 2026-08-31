"""Property tests for the scoring core — the SubMiner colour-priority model and the N+1 selector.

Invariants that pin the branch structure of `Scorer._style` and `mark_n_plus_one` so a mutation
(flipping a priority test, dropping an enable-flag guard, weakening the N+1 eligibility filter) turns
one of these red. Synthetic tokens (no tokenizer) keep it fast and deterministic. Companion to the
example-based test_coloring.py; drives `uv run poe mutate scoring`.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given

from saitenka.app.dict_meta import FreqDict, JlptDict
from saitenka.app.fsrs import KnownSnap
from saitenka.app.scoring import (
    FUNCTION_POS,
    Palette,
    Scorer,
    TokenVerdict,
    mark_n_plus_one,
)
from saitenka.app.tokenize import Token
from saitenka.app.wordlists import KnownWords

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


# --- concrete palette RGBA: pins _hex byte-slicing so the colour literals can't silently drift ------
# _hex mutants otherwise SURVIVE — everything else compares palette colours for *equality*, and both
# sides derive from the same _hex, so a mutated slice/constant stays self-consistent. One concrete
# assertion breaks that self-reference.


def test_palette_literals_are_concrete():
    p = Palette()
    assert p.base == (202, 211, 245, 255)  # #cad3f5
    assert p.known == (166, 218, 149, 255)  # #a6da95
    assert p.forgotten == (238, 153, 160, 255)  # #ee99a0
    assert p.n_plus_one == (198, 160, 246, 255)  # #c6a0f6
    assert p.freq_single == (245, 169, 127, 255)  # #f5a97f
    assert p.jlpt["N3"] == (249, 226, 175, 255)  # #f9e2af


# --- frequency colouring: the whole freq branch was under-tested (the example test only checks that
#     function words stay base, and skips when no freq zip is present) — so band indexing / rank guard
#     / freq_mode all survived. Drive a real FreqDict and assert the exact band colour. ----------------


def _kanji_token(surface: str = "本") -> Token:
    """A guaranteed content token (名詞, kanji so not kana-only, not a proper noun)."""
    return Token(surface, surface, "ほん", "名詞", 0, len(surface), "")


@given(rank=st.integers(min_value=1, max_value=10000))
def test_freq_banded_colour_matches_the_band(rank):
    """A ranked content word with no other signal gets the freq band colour FreqDict.band selects —
    pins the `band - 1` index and the `rank is not None` guard."""
    t = _kanji_token()
    sc = Scorer(
        known=KnownWords.from_set([]),
        freq=FreqDict({"本": rank, "ほん": rank}, "t"),
        enable_jlpt=False,
        enable_n_plus_one=False,
    )
    band = FreqDict.band(rank, sc.freq_top_x, len(PAL.freq_bands))
    assert band is not None  # rank ≤ top_x always bands
    s = sc.score_line([t])[0]
    assert s.color == PAL.freq_bands[band - 1]
    assert s.tag == f"freq-{band}"


def test_freq_single_mode_uses_one_colour():
    """freq_mode='single' colours every ranked word freq_single with tag 'freq' — no banding."""
    sc = Scorer(
        known=KnownWords.from_set([]),
        freq=FreqDict({"本": 500, "ほん": 500}, "t"),
        freq_mode="single",
        enable_jlpt=False,
        enable_n_plus_one=False,
    )
    s = sc.score_line([_kanji_token()])[0]
    assert s.color == PAL.freq_single
    assert s.tag == "freq"


def test_out_of_range_rank_falls_through_to_base():
    """A rank past top_x has no band → the word stays base (freq only colours the top_x)."""
    sc = Scorer(
        known=KnownWords.from_set([]),
        freq=FreqDict({"本": 99_999, "ほん": 99_999}, "t"),
        enable_jlpt=False,
        enable_n_plus_one=False,
    )
    s = sc.score_line([_kanji_token()])[0]
    assert s.color == PAL.base
    assert s.tag == "base"


# --- forgotten (FSRS) tint: the whole fsrs_snap path had no test — is_forgotten never fired. ---------


def test_forgotten_word_gets_the_forgotten_tint():
    """An FSRS 'forgotten' content word (not known, not N+1) resurfaces with the forgotten colour,
    between known and unknown."""
    snap = KnownSnap.of({"本": "forgotten"})
    sc = Scorer(
        known=KnownWords.from_set([]),
        fsrs_snap=snap,
        enable_freq=False,
        enable_jlpt=False,
        enable_n_plus_one=False,
    )
    s = sc.score_line([_kanji_token()])[0]
    assert s.color == PAL.forgotten
    assert s.tag.startswith("forgotten")


def test_fsrs_known_beats_forgotten_and_freq():
    """A word the snapshot marks 'known' takes the known colour even with a freq dict present."""
    snap = KnownSnap.of({"本": "known"})
    sc = Scorer(
        known=KnownWords.from_set([]),
        fsrs_snap=snap,
        freq=FreqDict({"本": 1}, "t"),
        enable_jlpt=False,
        enable_n_plus_one=False,
    )
    s = sc.score_line([_kanji_token()])[0]
    assert s.color == PAL.known


# --- multi-sentence N+1: each sentence is scored independently — pins the `start = i + 1` advance and
#     the sentence range, which single-sentence tests leave untouched. --------------------------------


def test_n1_marks_each_sentence_independently():
    """Two 。-separated sentences, each with exactly one eligible unknown among ≥3 content words, get
    their own N+1 mark. A broken sentence-boundary advance would merge them (2 candidates → no fire)."""

    def n(surface: str, pos: str = "名詞") -> Token:
        return Token(surface, surface, "よみ", pos, 0, len(surface), "")

    toks = [
        n("私"),
        n("本"),
        n("読む", "動詞"),
        n("。", "補助記号"),
        n("犬"),
        n("人"),
        n("待つ", "動詞"),
    ]
    known = [
        True,
        True,
        False,
        False,
        True,
        True,
        False,
    ]  # 読む (idx 2) and 待つ (idx 6) are the lone unknowns
    assert mark_n_plus_one(toks, known, min_words=3) == {2, 6}


# --- the verdict/palette seam: a classification and the color drawn from it never disagree ---------


def _expected_paint(verdict, palette: Palette):
    """Oracle mirroring the priority ladder independently of `Palette.style_for`, the way `_content`
    mirrors `is_content` above. Routing through `style_for` would only restate `score_line`'s own
    definition."""
    underline = palette.jlpt.get(verdict.jlpt) if verdict.jlpt else None
    if verdict.n_plus == 1:
        return palette.n_plus_one, underline
    if verdict.is_content and verdict.fsrs_state in {"forgotten", "learning", "young"}:
        return getattr(palette, verdict.fsrs_state), underline
    if verdict.is_content and verdict.is_known:
        return palette.known, underline
    if verdict.freq_single:
        return palette.freq_single, underline
    if verdict.freq_band is not None:
        return palette.freq_bands[verdict.freq_band - 1], underline
    return palette.base, underline


@given(
    _line(),
    st.booleans(),
    st.booleans(),
    st.booleans(),
    st.sampled_from(["banded", "single"]),
)
def test_every_drawn_colour_is_the_one_its_verdict_implies(line, jlpt_on, freq_on, known_on, mode):
    """Whatever the scorer is configured to consider, the colour a token is drawn in is the one an
    independent read of its verdict predicts — the invariant the split exists to make checkable.
    Before it, the classification and the colour were one pass with nothing to compare.
    """
    toks, _known = line
    sc = Scorer(
        known=KnownWords.from_set(["本", "犬"]),
        freq=FreqDict({"本": 500, "ほん": 500, "犬": 40_000}, "t"),
        jlpt=JlptDict({"本": "N5", "犬": "N4"}),
        enable_jlpt=jlpt_on,
        enable_freq=freq_on,
        enable_known=known_on,
        freq_mode=mode,
    )
    for style, verdict in zip(sc.score_line(toks), sc.verdict_line(toks), strict=True):
        assert (style.color, style.underline) == _expected_paint(verdict, sc.palette)


@given(_line())
def test_a_single_token_verdict_matches_its_line_verdict_but_for_n_plus_one(line):
    """`verdict(token)` is the per-token read the tooltip needs. It agrees with the line-scoped
    classification on every field N+1 does not depend on — N+1 alone needs the sentence."""
    toks, _known = line
    sc = Scorer(
        known=KnownWords.from_set(["本"]),
        jlpt=JlptDict({"本": "N5"}),
        enable_n_plus_one=False,  # the only field a lone token cannot know
    )
    for token, line_verdict in zip(toks, sc.verdict_line(toks), strict=True):
        assert sc.verdict(token) == line_verdict


def test_the_tag_is_derived_from_the_verdict_not_stored_beside_it():
    """A verdict built by hand reports the tag its fields imply — the negative control for the
    property above, which would still pass if `tag` were a field both paths happened to set."""
    assert TokenVerdict(is_content=True, n_plus=1, jlpt="N3").tag == "n+1/jlpt-N3"
    assert TokenVerdict(is_content=True, is_known=True).tag == "known"
    assert TokenVerdict(is_content=True, fsrs_state="forgotten").tag == "forgotten"
    assert TokenVerdict(is_content=True, freq_band=3).tag == "freq-3"
    assert TokenVerdict(is_content=True, freq_single=True).tag == "freq"
    assert TokenVerdict(is_content=True).tag == "base"
    # A function word never takes a state colour even when the collection says it is known.
    assert TokenVerdict(is_content=False, is_known=True).tag == "base"
