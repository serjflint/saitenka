"""A `term_meta` row keyed by the reading, and the ambiguity that admitting one introduces.

`_PRONUNCIATION_QUERY` / `_FREQUENCY_QUERY` used to select `m.term IN (terms)` with the terms taken
from matched headwords — for 本命 that is 本命, never ほんめい — so a dictionary keying an entry
under its kana reading contributed nothing to a kanji-written occurrence. Measured against the
dictionaries in this workspace: 13,941 of NHK 2016's rows are pure kana-keyed, covering **12,228
kanji-written jitendex headwords** whose pitch the product never showed, against 51,934 it did.

This was a regression, not a gap. Before #329 the accents came from `dict_meta.PitchSource`
(`term = ? OR reading = ?`) and the pills from `FreqSource.display`, both of which matched on the
reading. #329 moved the call sites onto the store's headword-pair semantics and left those readers
in place, uncalled — so their tests kept passing against the abandoned implementation, and nothing
recorded that the shipped behaviour had changed.

Selecting the readings too is only half the fix. A kana row identifies a *reading*, not a word, and
only 2,024 of those 12,228 headwords have a reading that maps to a single kanji headword — so
admitting them unconditionally would attach one accent to every homophone. `prefer_term_keyed`
resolves that per (dictionary, reading): a dictionary that answered under the term suppresses its
own kana row, and one that has only the kana row still contributes.
"""

from __future__ import annotations

import json
import zipfile

import dicthelp
import pytest
from saitenka_tokenize.japanese import Token

from saitenka.model import PitchAccent

_TOKEN = Token("本命", "本命", "ほんめい", "名詞", 0, 2)
_PITCH = {"reading": "ほんめい", "pitches": [{"position": 0}]}
_FREQ = {"reading": "ほんめい", "frequency": 8912}


def _term_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": "D", "format": 3}))
        zf.writestr(
            "term_bank_1.json", json.dumps([["本命", "ほんめい", "", "", 0, ["favourite"], 1, ""]])
        )
    return str(path)


def _meta_zip(path, title, mode, rows):
    """``rows`` are ``(key, payload)`` — the key is the kanji headword or its kana reading."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        zf.writestr(
            "term_meta_bank_1.json",
            json.dumps([[key, mode, payload] for key, payload in rows], ensure_ascii=False),
        )
    return str(path)


def _set(tmp_path, *, pitch=(), freq=()):
    """``pitch`` / ``freq`` are per-dictionary row lists, so a test can give one dictionary both
    keyings and another only the kana one."""
    return dicthelp.load_set(
        [_term_zip(tmp_path / "d.zip")],
        freq_zips=[
            _meta_zip(tmp_path / f"f{i}.zip", f"F{i}", "freq", rows) for i, rows in enumerate(freq)
        ],
        pitch_zips=[
            _meta_zip(tmp_path / f"p{i}.zip", f"P{i}", "pitch", rows)
            for i, rows in enumerate(pitch)
        ],
    )


def test_a_kanji_keyed_pitch_row_is_found(tmp_path):
    """Control: the path that always worked still does."""
    entry = _set(tmp_path, pitch=[[("本命", _PITCH)]]).entry_for(_TOKEN)
    assert entry.pitches == [("ほんめい", (PitchAccent(0),))]


def test_a_kana_keyed_pitch_row_is_found(tmp_path):
    """The regression, fixed: NHK 2016 holds 12,228 kanji-written words only this way."""
    entry = _set(tmp_path, pitch=[[("ほんめい", _PITCH)]]).entry_for(_TOKEN)
    assert entry.pitches == [("ほんめい", (PitchAccent(0),))]


def test_a_kana_keyed_frequency_row_reaches_the_pill(tmp_path):
    entry = _set(tmp_path, freq=[[("ほんめい", _FREQ)]]).entry_for(_TOKEN)
    assert [pill.value for pill in entry.freqs] == ["8912"]


def test_the_pill_and_the_blend_now_agree_on_a_kana_keyed_row(tmp_path):
    """They did not before: `rareness_rank` goes through `dict_meta.FreqSource`, which always tried
    the reading as a term, so the same word was scored as common while showing no pill."""
    dictionaries = _set(tmp_path, freq=[[("ほんめい", _FREQ)]])
    assert [pill.value for pill in dictionaries.entry_for(_TOKEN).freqs] == ["8912"]
    assert dictionaries.rareness_rank(_TOKEN) == 8912.0


def test_a_dictionary_that_answers_under_the_term_does_not_also_supply_its_kana_row(tmp_path):
    """The containment rule. Both rows match the lookup; only the precise one is used, so a
    dictionary that knows 本命 specifically is not diluted by its own reading-level entry."""
    entry = _set(
        tmp_path,
        pitch=[
            [
                ("本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}),
                ("ほんめい", {"reading": "ほんめい", "pitches": [{"position": 3}]}),
            ]
        ],
    ).entry_for(_TOKEN)
    assert entry.pitches == [("ほんめい", (PitchAccent(0),))]


def test_a_precise_dictionary_does_not_silence_a_kana_only_one(tmp_path):
    """Preference is per dictionary. P0 has the term, P1 has only the reading — both contribute,
    because suppressing P1 would lose the only answer it has."""
    entry = _set(
        tmp_path,
        pitch=[
            [("本命", {"reading": "ほんめい", "pitches": [{"position": 0}]})],
            [("ほんめい", {"reading": "ほんめい", "pitches": [{"position": 3}]})],
        ],
    ).entry_for(_TOKEN)
    assert entry.pitches == [("ほんめい", (PitchAccent(0),)), ("ほんめい", (PitchAccent(3),))]


def test_a_kana_row_whose_reading_disagrees_is_still_rejected(tmp_path):
    """Widening what is selected must not widen what is accepted: a row keyed by our reading but
    describing a different one is a different word."""
    entry = _set(
        tmp_path, pitch=[[("ほんめい", {"reading": "べつのよみ", "pitches": [{"position": 1}]})]]
    ).entry_for(_TOKEN)
    assert entry.pitches == []


@pytest.mark.parametrize("reading", ["ほんみょう", "ぽんめい"])
def test_a_kanji_row_with_the_wrong_reading_is_unaffected(tmp_path, reading):
    """Negative control for the pre-existing pair filter — unchanged by this fix."""
    entry = _set(
        tmp_path, pitch=[[("本命", {"reading": reading, "pitches": [{"position": 2}]})]]
    ).entry_for(_TOKEN)
    assert entry.pitches == []
