"""Does the live lookup path answer what the app-side `term_meta` readers answer?

`dict_meta.FreqSource` / `PitchSource` query `term_meta` with their own SQL, duplicating
`Translator.frequencies_for` / `pronunciations_for`. Two readers of one table is the shape that put
two schemas in one file (#472), so the duplicate is going away — but "they look equivalent" is a
reading of the code, not a fact about it, and the two SQL statements are demonstrably *not* the
same. `PitchSource.accents` matches `term = ? OR reading = ?` on one form at a time; the store
filters candidate `(term, reading)` headword pairs. Whether that difference is observable is what
this file answers, on real imported archives rather than by inspection.

Written before the retirement, so a divergence is a finding rather than a regression discovered
afterwards by whoever notices a pill went blank.
"""

from __future__ import annotations

import dicthelp
import pytest
from saitenka_dict import Translator
from saitenka_dict.sqlite_store import SqliteDictionaryStore

from saitenka.app.dict_meta import FreqSource, PitchSource


def _translator(db) -> Translator:
    return Translator(SqliteDictionaryStore(db.path))


def _import(tmp_path, name, mode, entries, **kw):
    zip_path = dicthelp.meta_zip(tmp_path / f"{name}.zip", name, mode, entries, **kw)
    db = dicthelp.db()
    return db, db.import_zip(zip_path, imported_at=dicthelp.AT)


# (term, reading) pairs are how the store is asked; the app-side readers take bare forms.
_HEADWORDS = (("本命", "ほんめい"),)


def test_a_pitch_entry_reads_the_same_through_both_paths(tmp_path):
    db, row = _import(
        tmp_path,
        "PitchDiff",
        "pitch",
        [["本命", {"reading": "ほんめい", "pitches": [{"position": 0}]}]],
    )
    legacy = PitchSource(db, row).accents(("本命", "ほんめい"), "ほんめい")
    live = _translator(db).pronunciations_for(_HEADWORDS, (row.title,))

    assert legacy is not None
    reading, accents = legacy
    assert [item.reading for item in live] == [reading]
    assert [position for item in live for position in item.pitch_positions] == [
        accent.position for accent in accents
    ]


def test_devoice_and_nasal_survive_both_paths(tmp_path):
    """The NHK/Kanjium shape — the only reason `PitchAccent` carries more than a position."""
    db, row = _import(
        tmp_path,
        "PitchRich",
        "pitch",
        [
            [
                "本命",
                {"reading": "ほんめい", "pitches": [{"position": 2, "devoice": [1], "nasal": [3]}]},
            ]
        ],
    )
    legacy = PitchSource(db, row).accents(("本命",), None)
    live = _translator(db).pronunciations_for(_HEADWORDS, (row.title,))

    assert legacy is not None
    _reading, accents = legacy
    assert [(a.position, a.devoice, a.nasal) for a in accents] == [(2, (1,), (3,))]
    assert [(i.pitch_positions, i.devoiced_morae, i.nasal_morae) for i in live] == [
        ((2,), (1,), (3,))
    ]


def test_a_reading_keyed_pitch_entry_now_reads_the_same_through_both_paths(tmp_path):
    """Was the divergence; is now the convergence.

    `PitchSource` matches `term = ? OR reading = ?` for each form it is given, so a bare surface
    form finds an entry whose `term` IS the reading. The store selected `m.term IN (headword terms)`
    and could not. `meta_lookup_terms` closed that, so the two agree and the retirement of these
    classes stops being a behaviour change.
    """
    db, row = _import(
        tmp_path,
        "PitchKana",
        "pitch",
        [["ほんめい", {"reading": "ほんめい", "pitches": [{"position": 0}]}]],
    )
    from saitenka.model import PitchAccent

    legacy = PitchSource(db, row).accents(("本命", "ほんめい"), "ほんめい")
    surface_only = _translator(db).pronunciations_for((("本命", "ほんめい"),), (row.title,))
    exact_pair = _translator(db).pronunciations_for((("ほんめい", "ほんめい"),), (row.title,))

    assert legacy == ("ほんめい", [PitchAccent(0)])
    # The surface form reaches it now — the reading is selected as a term as well.
    assert [item.pitch_positions for item in surface_only] == [(0,)]
    # ...and naming the kana directly is unchanged.
    assert [item.pitch_positions for item in exact_pair] == [(0,)]


@pytest.mark.parametrize(
    ("entries", "forms", "reading"),
    [
        ([["本命", {"reading": "ほんめい", "frequency": 8912}]], ("本命",), "ほんめい"),
        # SUW+LUW: two rows for one term. The blend takes the min; the store returns both.
        (
            [
                ["本命", {"reading": "ほんめい", "frequency": 14117}],
                ["本命", {"reading": "ほんめい", "frequency": 12813}],
            ],
            ("本命",),
            "ほんめい",
        ),
    ],
)
def test_the_blend_rank_matches_the_minimum_the_store_reports(tmp_path, entries, forms, reading):
    db, row = _import(tmp_path, "FreqDiff", "freq", entries)
    legacy = FreqSource(db, row).rank(forms, reading)
    live = _translator(db).frequencies_for(_HEADWORDS, (row.title,))

    ranks = [item.value for item in live if isinstance(item.value, int) and item.value > 0]
    assert legacy == min(ranks)


def test_a_kana_keyed_frequency_row_is_what_makes_the_retirement_a_no_op(tmp_path):
    """The case that decided the order of work — on the one method the product actually calls.

    `rareness_rank` blends `FreqSource.rank`, which tries the lemma, the surface form *and* the
    reading as terms. Before `meta_lookup_terms`, the store could not answer for a kana-keyed row at
    all, so retiring the blend onto `frequencies_for` would have dropped those dictionaries out of
    the mean — silently, and only for some words. Fixing the store first makes the retirement a
    behaviour-preserving deletion instead of a swap that has to be argued.
    """
    db, row = _import(
        tmp_path, "FreqKana", "freq", [["ほんめい", {"reading": "ほんめい", "frequency": 8912}]]
    )
    translator = _translator(db)

    assert FreqSource(db, row).rank(("本命", "ほんめい"), "ほんめい") == 8912
    assert [
        item.value for item in translator.frequencies_for((("本命", "ほんめい"),), (row.title,))
    ] == [8912]


def test_the_original_frequency_mode_is_not_on_any_package_type(tmp_path):
    """`occurrence_based` is the stated blocker — and the package *writes* the value it is said not
    to carry (`importer._rank_occurrences` persists `freqmode:<id>`). It is a missing accessor, not
    missing data, which is what makes the retirement tractable."""
    db, row = _import(
        tmp_path, "OccDiff", "freq", [["猫", 99999]], frequency_mode="occurrence-based"
    )

    from saitenka_dict import DictionaryDatabase

    assert FreqSource(db, row).occurrence_based is True
    assert db.meta_get(f"freqmode:{row.id}") == "occurrence"

    info = next(d for d in DictionaryDatabase(db.path).list_dictionaries() if d.title == row.title)
    assert dict(info.metadata).get("frequency_mode") is None, (
        "the package now reports the mode it writes — expose it on DictionaryInfo and drop FreqSource"
    )
