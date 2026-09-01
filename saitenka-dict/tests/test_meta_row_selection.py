"""Which `term_meta` rows a headword lookup selects, and which of those it keeps.

Both functions are pure, so they are tested here against constructed rows rather than through a
database — the SQL that feeds them is exercised in `test_sqlite_store.py`, and the end-to-end
behaviour in the application's `test_kana_keyed_meta_lookup.py`.
"""

from __future__ import annotations

import pytest
from saitenka_dict.sqlite_store import meta_lookup_terms, prefer_term_keyed

_HEADWORDS = (("本命", "ほんめい"),)


def _row(title, term, reading):
    return (title, term, reading, 0, None)


def test_both_the_term_and_the_reading_are_selected():
    assert meta_lookup_terms(_HEADWORDS) == ("本命", "ほんめい")


def test_selection_is_deduplicated_and_order_stable():
    """A kana-written word has term == reading; the IN list should not carry it twice."""
    assert meta_lookup_terms(((" ", ""), ("ねこ", "ねこ"), ("猫", "ねこ"))) == (" ", "ねこ", "猫")


def test_an_empty_form_is_never_selected():
    assert meta_lookup_terms((("", ""),)) == ()
    assert meta_lookup_terms(()) == ()


def test_a_term_keyed_row_is_kept():
    rows = [_row("NHK", "本命", "ほんめい")]
    assert prefer_term_keyed(rows, _HEADWORDS) == rows


def test_a_kana_keyed_row_is_kept_when_the_dictionary_has_nothing_better():
    rows = [_row("NHK", "ほんめい", "ほんめい")]
    assert prefer_term_keyed(rows, _HEADWORDS) == rows


def test_a_kana_keyed_row_is_dropped_when_the_same_dictionary_answered_by_term():
    """The containment rule: 2,863 words in NHK 2016 are keyed both ways, and taking both would
    show a reading-level accent beside the precise one."""
    precise = _row("NHK", "本命", "ほんめい")
    vague = _row("NHK", "ほんめい", "ほんめい")
    assert prefer_term_keyed([precise, vague], _HEADWORDS) == [precise]
    # Order of arrival must not decide it — the suppression is a property of the set.
    assert prefer_term_keyed([vague, precise], _HEADWORDS) == [precise]


def test_preference_is_per_dictionary():
    """A precise dictionary must not silence a vaguer one that is some word's only source."""
    precise = _row("NHK", "本命", "ほんめい")
    other = _row("Kanjium", "ほんめい", "ほんめい")
    assert prefer_term_keyed([precise, other], _HEADWORDS) == [precise, other]


def test_a_row_keyed_by_our_reading_but_describing_another_is_rejected():
    assert prefer_term_keyed([_row("NHK", "ほんめい", "べつのよみ")], _HEADWORDS) == []


def test_a_term_row_with_a_different_reading_is_rejected():
    """Pre-existing behaviour, unchanged: 本命/ほんみょう is not our word."""
    assert prefer_term_keyed([_row("NHK", "本命", "ほんみょう")], _HEADWORDS) == []


@pytest.mark.parametrize("keyed_by", ["本命", "ほんめい"])
def test_a_row_without_a_reading_is_identified_by_its_term_alone(keyed_by):
    """Some dictionaries omit the reading; the row still claims to be about whatever it is keyed
    under, and both keyings remain reachable."""
    rows = [_row("NHK", keyed_by, None)]
    assert prefer_term_keyed(rows, _HEADWORDS) == rows


def test_a_readingless_kana_row_still_loses_to_a_term_row():
    precise = _row("NHK", "本命", "ほんめい")
    vague = _row("NHK", "ほんめい", None)
    assert prefer_term_keyed([precise, vague], _HEADWORDS) == [precise]


def test_query_order_is_preserved():
    """Callers rely on `ORDER BY d.import_order, m.rowid` to rank dictionaries."""
    rows = [
        _row("A", "本命", "ほんめい"),
        _row("B", "本命", "ほんめい"),
        _row("C", "ほんめい", "ほんめい"),
    ]
    assert [row[0] for row in prefer_term_keyed(rows, _HEADWORDS)] == ["A", "B", "C"]


def test_an_unrelated_row_is_not_admitted_by_the_widened_selection():
    """The selection widened; the acceptance must not. 猫 is neither our term nor our reading."""
    assert prefer_term_keyed([_row("NHK", "猫", "ねこ")], _HEADWORDS) == []
