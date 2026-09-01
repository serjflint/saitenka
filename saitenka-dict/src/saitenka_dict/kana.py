"""Comparing two dictionaries' spellings of the same reading.

Frequency and pitch dictionaries are built independently and do not agree on how to write a reading.
BCCWJ writes ティーシャツ as てぃいしゃつ and プレーヤー as ぷれえやあ; Jiten writes ごみいれ as
ゴミいれ. Compared literally, none of those match the headword's reading, so the row is discarded and
the dictionary drops out of that word's pill row and out of the blended rank — measured at 15.9% of
headwords losing at least one whole frequency dictionary.

Matching on this key instead of on the raw string recovers them without loosening the rule that keeps
readings apart: 本命 read ほんみょう is still not 本命 read ほんめい. On the dictionaries this was
measured against, 20% of the discarded rows are the same reading spelled differently, and among
49,704 terms a dictionary itself gives more than one reading, the key collapses 120 — every one of
them a pair the dictionary listed twice in two scripts (らーめん/ラーメン, さん/サン).
"""

from __future__ import annotations

#: The vowel each kana ends on, for expanding the ー prolongation mark. The small vowels earn their
#: place: ティー folds to てぃー, whose ー lengthens the ぃ — without them it doubles the small kana
#: (てぃぃ) and misses the てぃい every hiragana-writing dictionary uses. A kana absent here (ん, っ,
#: ゖ) lengthens into a repeat of itself, which no dictionary writes either way.
_VOWEL: dict[str, str] = {
    **dict.fromkeys("あぁかさたなはまやゃらわゎがざだばぱ", "あ"),
    **dict.fromkeys("いぃきしちにひみりぎじぢびぴ", "い"),
    **dict.fromkeys("うぅくすつぬふむゆゅるぐずづぶぷ", "う"),
    **dict.fromkeys("えぇけせてねへめれげぜでべぺ", "え"),
    **dict.fromkeys("おぉこそとのほもよょろをごぞどぼぽ", "お"),
}
_KATAKANA_START, _KATAKANA_END = "ァ", "ヶ"
#: Katakana and hiragana are one contiguous block apart in Unicode, so the fold is one subtraction.
_TO_HIRAGANA = 0x60


def reading_key(reading: str) -> str:
    """A reading folded to hiragana with ー expanded, for comparing two dictionaries' spellings.

    Deliberately narrow. It does NOT fold づ/ず or ぢ/じ, and it does not touch the term — only the
    two disagreements actually observed in the wild get normalised away, so a rule that starts as a
    spelling fix cannot quietly become a fuzzy match.
    """
    folded: list[str] = []
    for char in reading:
        if _KATAKANA_START <= char <= _KATAKANA_END:
            folded.append(chr(ord(char) - _TO_HIRAGANA))
        elif char == "ー" and folded:
            folded.append(_VOWEL.get(folded[-1], folded[-1]))
        else:
            folded.append(char)
    return "".join(folded)
