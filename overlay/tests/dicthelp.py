"""Test helpers: import throwaway Yomitan zips into the per-test consolidated DB and hand back views.

The autouse ``_hermetic_dict_db`` fixture (conftest) points the default DB at a fresh tmp file per test,
so ``DictionaryDb.open()`` with no argument here lands on that isolated DB — nothing touches the user's
real ``data_dir()/dictionaries.sqlite``. Each ``load_*`` imports the given zip(s) and returns the same
view objects the runtime uses. Zip titles must be unique within a test (they key the dictionary)."""

from __future__ import annotations

import json
import zipfile

from overlay.app.dictdb import DictionaryDb
from overlay.app.dictionary import Dictionary, DictionarySet
from overlay.app.wordlists import FreqDict, FreqSource, JlptDict, PitchSource

AT = "2026-07-23T00:00:00"  # fixed imported_at — the store never stamps time itself


def term_zip(path, title, entries):
    """Write a minimal Yomitan v3 term-bank zip. ``entries``: [term, reading, glossary]."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [[t, r, "", "", 0, g, i + 1, ""] for i, (t, r, g) in enumerate(entries)]
        zf.writestr("term_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def meta_zip(path, title, mode, entries):
    """Write a minimal Yomitan term_meta_bank zip. ``entries``: [term, data]; ``mode``: freq|pitch."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.json", json.dumps({"title": title, "format": 3}))
        bank = [[t, mode, data] for t, data in entries]
        zf.writestr("term_meta_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return str(path)


def db() -> DictionaryDb:
    """Open the per-test hermetic consolidated DB."""
    return DictionaryDb.open()


def load_dict(zip_path, *, on: DictionaryDb | None = None) -> Dictionary:
    d = on or db()
    return Dictionary(d, d.import_zip(zip_path, imported_at=AT))


def load_set(
    dict_zips=(), freq_zips=(), pitch_zips=(), *, on: DictionaryDb | None = None
) -> DictionarySet:
    d = on or db()
    dict_rows = [d.import_zip(z, imported_at=AT, import_order=i) for i, z in enumerate(dict_zips)]
    freq_rows = [d.import_zip(z, imported_at=AT) for z in freq_zips]
    pitch_rows = [d.import_zip(z, imported_at=AT) for z in pitch_zips]
    return DictionarySet.from_rows(d, dict_rows, freq_rows, pitch_rows)


def load_freqsource(zip_path, *, on: DictionaryDb | None = None) -> FreqSource:
    d = on or db()
    return FreqSource(d, d.import_zip(zip_path, imported_at=AT))


def load_pitchsource(zip_path, *, on: DictionaryDb | None = None) -> PitchSource:
    d = on or db()
    return PitchSource(d, d.import_zip(zip_path, imported_at=AT))


def load_freqdict(zip_path, *, on: DictionaryDb | None = None) -> FreqDict:
    d = on or db()
    return FreqDict.from_db(d, d.import_zip(zip_path, imported_at=AT))


def load_jlpt(*, on: DictionaryDb | None = None) -> JlptDict:
    return JlptDict.load(on or db())
