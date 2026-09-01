"""A `term_meta` row keyed by the reading is unreachable for a word written in kanji.

`_PRONUNCIATION_QUERY` / `_FREQUENCY_QUERY` select `m.term IN (terms)`, and the terms come from
matched headwords — for 本命 that is 本命, never ほんめい. A pitch or frequency dictionary that keys
an entry under its kana reading therefore contributes nothing to a kanji-written occurrence.

Measured against the dictionaries in this workspace rather than argued from the SQL: of 287,782
kanji-written jitendex headwords, 51,934 find pitch in NHK 2016 today and a further **12,228 have
pitch in that same dictionary only under the kana term** — about one in five of the words NHK can
actually answer for. 13,941 of NHK's rows are pure kana-keyed (`term == reading`).

The frequency half of this is worse than a miss, because the two readers disagree *with each other*
inside one running product: `rareness_rank` goes through `dict_meta.FreqSource`, which tries the
reading as a term and finds the row, while the pill goes through `frequencies_for` and does not. The
same word is ranked as common by the scorer and shows no frequency pill.

Open bug, so the assertions are `xfail(strict=True)`: they flip to failures the moment it is fixed.
The non-xfail tests around them are the controls — they prove the fixtures and the lookup path work
when the entry happens to be keyed the way the query expects.
"""

from __future__ import annotations

import json
import zipfile

import dicthelp
import pytest
from saitenka_tokenize.japanese import Token

from saitenka.model import PitchAccent

_TOKEN = Token("本命", "本命", "ほんめい", "名詞", 0, 2)


def _term_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "D", "format": 3}))
        zf.writestr(
            "term_bank_1.json", json.dumps([["本命", "ほんめい", "", "", 0, ["favourite"], 1, ""]])
        )
    return str(path)


def _meta_zip(path, title, mode, key, payload):
    """One `term_meta` row keyed by ``key`` — the kanji headword or its kana reading."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        zf.writestr("term_meta_bank_1.json", json.dumps([[key, mode, payload]], ensure_ascii=False))
    return str(path)


_PITCH = {"reading": "ほんめい", "pitches": [{"position": 0}]}
_FREQ = {"reading": "ほんめい", "frequency": 8912}


def _set(tmp_path, *, pitch_key=None, freq_key=None):
    terms = [_term_zip(tmp_path / "d.zip")]
    pitch = [_meta_zip(tmp_path / "p.zip", "P", "pitch", pitch_key, _PITCH)] if pitch_key else []
    freq = [_meta_zip(tmp_path / "f.zip", "F", "freq", freq_key, _FREQ)] if freq_key else []
    return dicthelp.load_set(terms, freq_zips=freq, pitch_zips=pitch)


def test_a_kanji_keyed_pitch_row_is_found(tmp_path):
    """Control: the fixture and the lookup path work when the row is keyed the expected way."""
    entry = _set(tmp_path, pitch_key="本命").entry_for(_TOKEN)
    assert entry.pitches == [("ほんめい", (PitchAccent(0),))]


@pytest.mark.xfail(
    reason="kana-keyed term_meta: the query selects m.term IN (headword terms), so a row keyed by "
    "the reading is unreachable for a kanji-written word (12,228 such words in NHK 2016)",
    strict=True,
)
def test_a_kana_keyed_pitch_row_is_found(tmp_path):
    entry = _set(tmp_path, pitch_key="ほんめい").entry_for(_TOKEN)
    assert entry.pitches == [("ほんめい", (PitchAccent(0),))]


def test_a_kanji_keyed_frequency_row_reaches_both_the_pill_and_the_blend(tmp_path):
    """Control for the pair below: keyed as the query expects, the two readers agree."""
    dictionaries = _set(tmp_path, freq_key="本命")
    entry = dictionaries.entry_for(_TOKEN)
    assert [pill.value for pill in entry.freqs] == ["8912"]
    assert dictionaries.rareness_rank(_TOKEN) == 8912.0


def test_the_blend_still_sees_a_kana_keyed_frequency_row(tmp_path):
    """Not a bug — the half that works, and the reason the retirement is not a straight swap.

    `rareness_rank` goes through `dict_meta.FreqSource`, which matches `term = ?` against the
    lemma, the surface form and the reading in turn. Moving it onto `frequencies_for` without
    pairing every form as a term would take this from working to broken.
    """
    assert _set(tmp_path, freq_key="ほんめい").rareness_rank(_TOKEN) == 8912.0


@pytest.mark.xfail(
    reason="kana-keyed term_meta: the frequency pill misses a row the rareness blend finds, so one "
    "product ranks the word as common while showing no pill for it",
    strict=True,
)
def test_a_kana_keyed_frequency_row_reaches_the_pill(tmp_path):
    entry = _set(tmp_path, freq_key="ほんめい").entry_for(_TOKEN)
    assert [pill.value for pill in entry.freqs] == ["8912"]
