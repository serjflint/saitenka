"""The active profile's language routes the deinflection chain (#254 W2 wiring).

``DictionarySet`` carries the profile's main language and passes it to the GPL ``inflection_chain``
chokepoint, so a French profile shows French inflection reasons and a Japanese one shows Japanese —
asserted on the observable ``Entry.inflection_chain``, through the real ``entry_for`` (no dicts needed:
the chain is computed from the token's surface→lemma, independent of dictionary hits).
"""

from __future__ import annotations

import pytest
from overlay.app.dictionary import DictionarySet
from overlay.app.tokenize import Token

pytest.importorskip("saitenka_deinflect")  # chain is empty without the GPL add-on; skip if absent


def _token(surface: str, lemma: str) -> Token:
    return Token(surface=surface, lemma=lemma, reading="", pos="WORD", start=0, end=len(surface))


def test_french_profile_shows_french_inflection_chain():
    ds = DictionarySet(dicts=[], language="fr")
    entry = ds.entry_for(_token("parlons", "parler"))
    assert entry.inflection_chain == ["present indicative"]


def test_japanese_default_does_not_deinflect_a_french_surface():
    # The same French surface under the JP default has no path — the language field is what selects the
    # rule set, so a mis-scoped profile can't silently borrow the other language's grammar.
    ds = DictionarySet(dicts=[], language="jp")
    assert ds.entry_for(_token("parlons", "parler")).inflection_chain == []


def test_second_language_lookup_folds_in_the_deinflected_dictionary_form():
    # The Latin tokenizer has no lemmatizer, so its lemma is the inflected surface. The dictionary form
    # to actually look up (parapluies → parapluie) MUST come from the deinflector, or an inflected word
    # finds nothing but "plural of …" — the live bug on "parapluies".
    ds = DictionarySet(dicts=[], language="fr")
    assert "parapluie" in ds._deinflected_candidates("parapluies")
    assert "chat" in ds._deinflected_candidates("chats")


def test_japanese_lemma_is_never_deinflect_expanded():
    # JP's MeCab lemma is already the dict form; expanding it would change the byte-identical JP path.
    ds = DictionarySet(dicts=[], language="jp")
    assert ds._deinflected_candidates("食べた") == ()


def test_not_found_message_is_english_for_a_second_language_profile():
    # A French learner must not see a Japanese "not found" sentence (live bug on "ça").
    ds = DictionarySet(dicts=[], language="fr")
    text = ds.entry_for(_token("zzqxq", "zzqxq")).defs[0].content[0]
    assert "not found" in text.lower()


def test_not_found_message_stays_japanese_for_the_default_profile():
    ds = DictionarySet(dicts=[], language="jp")
    assert "見つかり" in ds.entry_for(_token("食べた", "食べる")).defs[0].content[0]
