from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_tokenize.japanese import tokenize


@pytest.mark.parametrize("merge", [False, True])
@given(
    word=st.sampled_from(
        ["が", "だ", "ぱ", "ギター", "パンダ", "泳ぐ", "食べた", "泳いでいる", "読んでしまう"]
    )
)
def test_decomposed_voicing_retains_dictionary_identity_and_original_offsets(word, merge):
    source = "猫 " + unicodedata.normalize("NFD", word) + " 犬"
    normalized = "猫 " + word + " 犬"

    actual = tokenize(source, strip_furigana=False, merge=merge)
    expected = tokenize(normalized, strip_furigana=False, merge=merge)

    assert [(t.lemma, t.reading, t.pos) for t in actual] == [
        (t.lemma, t.reading, t.pos) for t in expected
    ]
    assert [unicodedata.normalize("NFC", t.surface) for t in actual] == [
        t.surface for t in expected
    ]
    assert all(source[t.start : t.end] == t.surface for t in actual)
    assert not any(t.surface.startswith(("\u3099", "\u309a")) for t in actual)


@pytest.mark.parametrize("source", ["\u3099猫", "木\u309a", "か \u3099", "か\u3099\u3099"])
def test_noncomposable_voicing_marks_are_not_swallowed(source):
    tokens = tokenize(source, strip_furigana=False, merge=False)

    assert "".join(t.surface for t in tokens).replace(" ", "") == source.replace(" ", "")
    assert all(source[t.start : t.end] == t.surface for t in tokens)
