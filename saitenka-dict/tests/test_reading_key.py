"""Which spellings of a reading count as the same reading."""

from __future__ import annotations

import pytest
from saitenka_dict.kana import reading_key
from saitenka_dict.sqlite_store import prefer_term_keyed


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("ティーシャツ", "てぃいしゃつ"),  # BCCWJ writes its readings in hiragana, ー expanded
        ("プレーヤー", "ぷれえやあ"),
        ("ゴミいれ", "ごみいれ"),  # Jiten mixes the two scripts within one reading
        ("ラーメン", "らーめん"),
        ("エヌきょう", "えぬきょう"),
    ],
)
def test_two_spellings_of_one_reading_share_a_key(left, right):
    assert reading_key(left) == reading_key(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("ほんめい", "ほんみょう"),  # 本命 — two readings, two words
        ("あそこ", "あしこ"),
        ("あせぼ", "あせび"),
        ("こうし", "こうじ"),  # the fold is not a fuzzy match: dakuten still separates
        ("ちょうど", "ちょっと"),
    ],
)
def test_two_readings_keep_distinct_keys(left, right):
    assert reading_key(left) != reading_key(right)


def test_a_prolongation_mark_with_nothing_to_lengthen_is_left_alone():
    """Leading ー is malformed, not a crash — community dictionaries carry rows like this."""
    assert reading_key("ー") == "ー"
    assert reading_key("") == ""


def test_the_fold_is_idempotent():
    for text in ("ティーシャツ", "ぷれえやあ", "ラーメン", "ほんめい"):
        assert reading_key(reading_key(text)) == reading_key(text)


_HEADWORDS = (("Ｔシャツ", "ティーシャツ"),)


def _row(title, term, reading):
    return (title, term, reading, 0, None)


def test_a_row_whose_reading_differs_only_in_spelling_is_kept():
    """BCCWJ was dropped out of the pill row and the blended rank for this word entirely."""
    row = _row("BCCWJ", "Ｔシャツ", "てぃいしゃつ")
    assert prefer_term_keyed([row], _HEADWORDS) == [row]


def test_a_row_with_a_genuinely_different_reading_is_still_rejected():
    """The negative control. Widening the match must not turn it into "any row for this term"."""
    assert prefer_term_keyed([_row("NHK", "本命", "ほんみょう")], (("本命", "ほんめい"),)) == []


def test_the_suppression_rule_also_compares_by_key():
    """A dictionary that answered under the term suppresses its own kana row — including when the
    two rows spell the shared reading differently, which is otherwise a duplicate pill."""
    precise = _row("Jiten", "Ｔシャツ", "てぃいしゃつ")
    vague = _row("Jiten", "ティーシャツ", "ティーシャツ")
    assert prefer_term_keyed([precise, vague], _HEADWORDS) == [precise]
