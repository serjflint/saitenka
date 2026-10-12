"""Public dictionary lookup preserves profile language, deinflection and entry priority."""

from __future__ import annotations

import dicthelp
import pytest
from saitenka_tokenize.japanese import Token

from saitenka.app.dictionary import DictionarySet

pytest.importorskip("saitenka_deinflect")  # chain is empty without the GPL add-on; skip if absent


def _token(surface: str, lemma: str) -> Token:
    return Token(surface=surface, lemma=lemma, reading="", pos="WORD", start=0, end=len(surface))


@pytest.mark.parametrize(
    ("language", "surface", "lemma", "chain", "message"),
    [
        ("fr", "parlons", "parler", ["present indicative"], "not found"),
        ("jp", "parlons", "parler", [], "見つかり"),
        ("jp-JP", "食べた", "食べる", ["-た"], "見つかり"),
        ("de-CH", "Häuser", "Häuser", [], "not found"),
        ("fr", "zzqxq", "zzqxq", [], "not found"),
        ("jp", "食べた", "食べる", ["-た"], "見つかり"),
    ],
)
def test_missing_dictionary_entry_preserves_profile_language_and_inflection(
    language, surface, lemma, chain, message
):
    entry = DictionarySet(dicts=[], language=language).entry_for(_token(surface, lemma))

    assert entry.inflection_chain == chain
    assert message in str(entry.defs[0].content).lower()


def test_japanese_lemma_is_never_deinflect_expanded(tmp_path):
    dictionary = dicthelp.load_set(
        [dicthelp.term_zip(tmp_path / "jp.zip", "Japanese", [["食べる", "", ["to eat"]]])]
    )

    entry = dictionary.entry_for(_token("食べた", "食べた"))

    assert entry.defs[0].dict_name == "—"


def test_jp_ranking_keeps_longest_match_first_with_no_preferred_set(tmp_path):
    dictionary = dicthelp.load_set(
        [
            dicthelp.term_zip(
                tmp_path / "jp.zip",
                "Japanese",
                [
                    ["数", "", ["number"]],
                    ["数ある", "", ["numerous"]],
                ],
            )
        ]
    )

    entry = dictionary.entry_for(_token("数", "数"), extra_terms=("数ある",))

    assert (entry.headword, entry.defs[0].content[0]) == (["数ある"], "numerous")
