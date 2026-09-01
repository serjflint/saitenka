"""The package's own suite: what a consumer that is not Saitenka can rely on.

Deliberately not a copy of the overlay's tokenizer tests — those pin unidic-lite's segmentation as
goldens and belong with the app that re-blesses them. These pin the PACKAGE's contract: the strategy
registry, the dictionary-free seams, and the two kana predicates that are its public surface.
"""

from __future__ import annotations

import pytest
from saitenka_tokenize import (
    Token,
    get_tokenizer,
    has_kanji,
    is_kana,
    kata_to_hira,
    merge_dict_compounds,
    phrase_terms,
    query_token,
    register_tokenizer,
)


def _tok(surface: str, pos: str = "名詞", start: int = 0) -> Token:
    return Token(surface, surface, "", pos, start, start + len(surface))


def test_kata_to_hira_folds_only_the_katakana_letters():
    assert kata_to_hira("ヨム") == "よむ"
    assert kata_to_hira("よむ") == "よむ"
    assert kata_to_hira("ジョン・スミス") == "じょん・すみす"  # the nakaguro is not a letter


def test_has_kanji_spans_the_supplementary_planes():
    assert has_kanji("漢字")
    assert has_kanji("𩸽")  # U+29E3D, CJK Ext B — an astral surrogate pair
    assert not has_kanji("ほっけ")


def test_is_kana_and_is_kana_only_disagree_about_the_separators():
    """The two are different questions. `is_kana` is the character class the furigana aligners want;
    `Token.is_kana_only` excludes ・ and ゠ so a name is not mistaken for one kana-only word."""
    assert is_kana("・") and is_kana("゠")
    assert not _tok("ジョン・スミス").is_kana_only
    assert _tok("ジョン").is_kana_only


def test_a_query_is_looked_up_whole_and_never_segmented():
    """A cross-reference link targets its text as ONE term. Tokenizing and taking the first token
    capped それにしては to それ, so every compound link resolved to the wrong entry."""
    token = query_token("それにしては")
    assert token is not None
    assert (token.surface, token.lemma, token.end) == ("それにしては", "それにしては", 6)
    assert query_token("   ") is None


def test_the_registry_serves_a_strategy_by_name_and_rejects_an_unknown_one():
    assert get_tokenizer("unidic").name == "unidic"
    assert get_tokenizer("latin").name == "latin"
    with pytest.raises(ValueError, match="unknown tokenizer"):
        get_tokenizer("klingon")


def test_a_new_language_can_point_at_a_registered_strategy_with_no_code():
    register_tokenizer("borrowed", lambda: get_tokenizer("latin"))
    assert get_tokenizer("borrowed").name == "latin"


def test_content_and_skippable_are_not_complements():
    """A grammatical particle is neither: not worth mining, still worth hit-testing. Testing `not
    is_content` in place of `is_skippable` would quietly drop it from annotation."""
    tokenizer = get_tokenizer("unidic")
    particle = _tok("を", pos="助詞")
    assert not tokenizer.is_content(particle)
    assert not tokenizer.is_skippable(particle)


def test_attestation_is_a_callable_so_the_package_stays_dictionary_free():
    """`merge_dict_compounds` and `phrase_terms` ask whether a form exists through a callable they are
    handed. That is what lets this package ship without a dictionary."""
    tokens = [_tok("応急", start=0), _tok("処置", start=2)]
    asked: list[object] = []

    def exists(forms):
        asked.append(tuple(forms))
        return {"応急処置"}

    merged = merge_dict_compounds(tokens, exists)
    assert [t.surface for t in merged] == ["応急処置"]
    assert asked == [("応急処置",)]  # ONE batched probe for the line, not a lookup per span

    assert phrase_terms(tokens, 0, lambda term: term == "応急処置") == (["応急処置"], 0, 2)
    assert phrase_terms(tokens, 0, lambda _term: False) is None
