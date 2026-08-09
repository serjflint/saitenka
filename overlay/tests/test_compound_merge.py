"""Dictionary-attested compound merging (issue #94): adjacent tokens whose joined span is an exact
dictionary headword collapse into ONE token — Yomitan longest-match at the token level, so a compound
unidic over-splits (応急+処置, 満員+電車, 走り+出した) is one hover / hit-test / color / mine unit.

The seam is a batch existence probe (``exists(forms) -> set[str]``), kept dict-free like ``phrase_terms``'
``has_term`` — most tests pass a plain set-returning lambda; ``terms_exist`` is covered against a real
imported dictionary at the bottom.
"""

from __future__ import annotations

import dicthelp
from overlay.app.controller import Reader
from overlay.app.scoring import Palette, Scorer
from overlay.app.subtitle_render import NullRenderer
from overlay.app.tokenize import Token, merge_dict_compounds, tokenize
from overlay.app.wordlists import KnownWords
from util import FakeIPC


def _at(
    surface: str, start: int, pos: str = "名詞", lemma: str | None = None, reading: str = ""
) -> Token:
    """A token located at ``start`` — offsets must be exact-adjacent for a span to merge."""
    return Token(surface, lemma or surface, reading or surface, pos, start, start + len(surface))


def _exists(*headwords: str):
    """A batch existence seam that attests exactly the given headwords."""
    hw = set(headwords)
    return lambda forms: {f for f in forms if f in hw}


# --- the noun compound (surface == lemma): the headline fragment fix ------------------------------


def test_noun_compound_merges_into_one_token():
    toks = [_at("応急", 0, reading="おうきゅう"), _at("処置", 2, reading="しょち")]
    merged = merge_dict_compounds(toks, _exists("応急処置"))
    assert len(merged) == 1
    (t,) = merged
    assert t.surface == "応急処置"
    assert t.lemma == "応急処置"  # drives the dictionary lookup
    assert (
        t.reading == "おうきゅうしょち"
    )  # concatenated component readings (no cross-particle rendaku)
    assert (t.start, t.end) == (0, 4)  # one contiguous hit-test span
    assert t.pos == "名詞"  # head POS for a nominal compound → stays content/hoverable
    assert t.is_content


def test_unattested_span_is_left_as_fragments():
    toks = [_at("応急", 0), _at("処置", 2)]
    assert [t.surface for t in merge_dict_compounds(toks, _exists())] == ["応急", "処置"]  # no hit


# --- the verb compound: the tail is deinflected to its dict form for the probe --------------------


def test_verb_compound_probes_the_deinflected_tail():
    # 走り出した arrives (post merge_inflected) as 走り(動詞) + 出した(動詞, lemma 出す): the candidate the
    # dictionary is probed with is the DICT form 走り出す, not the inflected surface join 走り出した.
    seen: list[list[str]] = []

    def exists(forms):
        seen.append(list(forms))
        return {"走り出す"}

    toks = [
        _at("走り", 0, "動詞", reading="はしり"),
        _at("出した", 2, "動詞", lemma="出す", reading="だした"),
    ]
    (t,) = merge_dict_compounds(toks, exists)
    assert seen == [["走り出す"]]  # probed the deinflected headword
    assert t.surface == "走り出した"  # display keeps the inflected surface
    assert t.lemma == "走り出す"  # lookup uses the dict form
    assert t.reading == "はしりだした"  # contextual (inflected) reading for furigana + affinity
    assert (
        t.pos == "動詞" and t.pos2 == ""
    )  # inflectable tail → verb, so downstream treats it as one


def test_inflected_surface_join_alone_never_licenses_a_merge():
    # only the deinflected 走り出す is a headword; the raw surface join 走り出した is not → no merge, so an
    # inflected string can never become a card front.
    toks = [_at("走り", 0, "動詞"), _at("出した", 2, "動詞", lemma="出す")]
    assert [t.surface for t in merge_dict_compounds(toks, _exists("走り出した"))] == [
        "走り",
        "出した",
    ]


def test_real_tokenizer_verb_compound_merges_end_to_end():
    # the LIVE pipeline (not hand-built tokens): tokenize → merge_inflected leaves 走り(動詞) +
    # 出した(動詞, lemma 出す), then the compound pass probes the deinflected 走り出す and merges the whole
    # inflected word. Locks in the merge_inflected→compound-pass interaction against the live unidic split
    # (a unidic bump that re-segments this legitimately re-blesses here, like the other tokenizer goldens).
    merged = merge_dict_compounds(tokenize("彼は走り出した"), _exists("走り出す"))
    by = {t.surface: t for t in merged}
    assert "走り出した" in by  # ONE hover/mine unit — not 走り + 出した
    assert by["走り出した"].lemma == "走り出す"  # the dict form drives lookup
    assert by["走り出した"].pos == "動詞"


# --- longest-match-first + greedy left-to-right consumption ---------------------------------------


def test_longest_span_wins_over_a_shorter_prefix():
    toks = [_at("満員", 0), _at("電車", 2), _at("内", 4)]
    # both 満員電車 (2 tok) and 満員電車内 (3 tok) attest → the longest is taken
    (t,) = merge_dict_compounds(toks, _exists("満員電車", "満員電車内"))
    assert t.surface == "満員電車内"
    assert (t.start, t.end) == (0, 5)


def test_greedy_consumes_the_span_then_resumes_after_it():
    # 応急処置 merges (0-1); the scan resumes at 済み and does not re-enter the consumed tokens.
    toks = [_at("応急", 0), _at("処置", 2), _at("済み", 4)]
    merged = merge_dict_compounds(toks, _exists("応急処置"))
    assert [t.surface for t in merged] == ["応急処置", "済み"]


def test_advances_past_an_unmergeable_content_head_to_a_later_pair():
    # A B C, all content, adjacent; only B+C attests → the head A (no hit) is emitted alone and the scan
    # advances to B where 電車内 merges. Exercises the "content head, no span from here" branch.
    toks = [_at("彼", 0), _at("電車", 1), _at("内", 3)]
    merged = merge_dict_compounds(toks, _exists("電車内"))
    assert [t.surface for t in merged] == ["彼", "電車内"]


# --- guardrails: boundaries, caps, adjacency, span start ------------------------------------------


def test_never_crosses_a_particle_boundary():
    # 本 を 読む — even if a lambda claimed 本を were a term, を is 助詞 (not content) so the span breaks
    # there, exactly the 格/係助詞 boundary merge_inflected respects.
    toks = [_at("本", 0), _at("を", 1, "助詞"), _at("読む", 2, "動詞")]
    got = merge_dict_compounds(toks, _exists("本を", "本を読む"))
    assert [t.surface for t in got] == ["本", "を", "読む"]


def test_never_crosses_an_auxiliary_boundary():
    toks = [_at("元気", 0), _at("だ", 2, "助動詞")]
    assert [t.surface for t in merge_dict_compounds(toks, _exists("元気だ"))] == ["元気", "だ"]


def test_span_token_cap_blocks_an_over_long_join():
    toks = [_at("あ", i, reading="あ") for i in range(5)]  # 5 tokens, all content, all adjacent
    # even attested, a 5-token span exceeds the 4-token cap and never forms
    assert [t.surface for t in merge_dict_compounds(toks, _exists("あああああ"))] == ["あ"] * 5


def test_span_char_cap_blocks_a_long_katakana_run():
    a = _at("ア" * 10, 0)  # 10 chars
    b = _at("イ" * 10, 10)  # +10 = 20 chars, over the 16-char cap
    got = merge_dict_compounds([a, b], _exists("ア" * 10 + "イ" * 10))
    assert [t.surface for t in got] == ["ア" * 10, "イ" * 10]  # never joined


def test_non_adjacent_tokens_do_not_merge():
    # a gap between the surfaces (start != previous end — e.g. a stripped space or the next source
    # line, whose per-line offsets restart) is not a compound
    toks = [_at("応急", 0), _at("処置", 5)]  # 処置 starts at 5, not 2
    assert [t.surface for t in merge_dict_compounds(toks, _exists("応急処置"))] == ["応急", "処置"]


def test_span_must_start_at_a_content_token():
    # a leading particle is not a valid span start, so 、応急処置 never merges from index 0
    toks = [_at("と", 0, "助詞"), _at("応急", 1), _at("処置", 3)]
    merged = merge_dict_compounds(toks, _exists("と応急処置", "応急処置"))
    assert [t.surface for t in merged] == [
        "と",
        "応急処置",
    ]  # merges from the content token, not the と


def test_short_lines_pass_through_untouched():
    lone = [_at("猫", 0)]
    assert merge_dict_compounds(lone, _exists("猫")) is lone
    assert merge_dict_compounds([], _exists()) == []


# --- the open question: one merged unit → one coloring unit ---------------------------------------


def test_merged_compound_is_coloured_as_one_unit():
    # 満員電車: 満員 is a known word, 電車 is not. Once merged, the COMPOUND's own known-state drives the
    # single colour — it is not painted from either fragment (issue #94's coloring guardrail vs #20/#26).
    toks = merge_dict_compounds([_at("満員", 0), _at("電車", 2)], _exists("満員電車"))
    scorer = Scorer(known=KnownWords.from_set(["満員"]), enable_freq=False)
    styles = scorer.score_line(toks)
    assert len(styles) == 1  # one token → one style, not two fragment colours
    assert styles[0].color == Palette().base  # 満員電車 (not 満員) is the lookup key → not "known"


# --- controller wiring: the merged compound reaches reader.tokens ---------------------------------


class _ExistsDS:
    """A dict set exposing only the terms_exist capability the controller probes for."""

    def __init__(self, *headwords: str) -> None:
        self._hw = set(headwords)

    def terms_exist(self, forms):
        return {f for f in forms if f in self._hw}


def test_controller_tokens_carry_the_merged_compound(monkeypatch):
    reader = Reader(FakeIPC(), dict_set=_ExistsDS("応急処置"))
    reader.osd = (1920, 1080)
    monkeypatch.setattr(reader, "renderer", NullRenderer())
    # decouple from the live unidic split: the cue tokenises to 応急 + 処置
    monkeypatch.setattr(reader.tokenizer, "tokenize", lambda _ln: [_at("応急", 0), _at("処置", 2)])
    reader.set_subtitle("応急処置")
    assert [t.surface for t in reader.tokens] == ["応急処置"]  # ONE hover/hit-test/mine unit
    assert reader.tokens[0].lemma == "応急処置"


def test_controller_leaves_fragments_when_dict_set_has_no_probe(monkeypatch):
    reader = Reader(FakeIPC(), dict_set=object())  # no terms_exist → merge is skipped
    reader.osd = (1920, 1080)
    monkeypatch.setattr(reader, "renderer", NullRenderer())
    monkeypatch.setattr(reader.tokenizer, "tokenize", lambda _ln: [_at("応急", 0), _at("処置", 2)])
    reader.set_subtitle("応急処置")
    assert [t.surface for t in reader.tokens] == ["応急", "処置"]


# --- the batch existence probe against a real imported dictionary ---------------------------------


def test_terms_exist_returns_only_exact_headwords(tmp_path):
    zip_path = dicthelp.term_zip(
        tmp_path / "d.zip",
        "D",
        [("応急処置", "おうきゅうしょち", ["first aid"]), ("電車", "でんしゃ", ["train"])],
    )
    ds = dicthelp.load_set(dict_zips=[zip_path])
    got = ds.terms_exist(["応急処置", "電車", "応急", "満員電車"])
    assert got == {"応急処置", "電車"}  # only the attested headwords; fragments/misses excluded


def test_terms_exist_excludes_a_reading_only_coincidence(tmp_path):
    # でんしゃ is 電車's READING, not a headword. A candidate string that merely coincides with a reading
    # must NOT license a merge (unlike has_term, whose reading hits are wanted for the kanji fallback).
    zip_path = dicthelp.term_zip(tmp_path / "d.zip", "D", [("電車", "でんしゃ", ["train"])])
    ds = dicthelp.load_set(dict_zips=[zip_path])
    assert ds.terms_exist(["でんしゃ"]) == set()
    assert ds.terms_exist(["電車"]) == {"電車"}


def test_terms_exist_on_empty_input_is_empty(tmp_path):
    ds = dicthelp.load_set(
        dict_zips=[dicthelp.term_zip(tmp_path / "d.zip", "D", [("猫", "ねこ", ["cat"])])]
    )
    assert ds.terms_exist([]) == set()
    assert ds.terms_exist(["", None]) == set()  # empty/None forms filtered out
