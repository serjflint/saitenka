"""Parity check: our tooltip vs SubMiner for the same episode words.

Structural (not pixel) comparison — asserts our render carries the same frequency dictionaries and
the same inflection chain SubMiner shows. Skipped unless the configured dictionaries are present
(they're large and live outside the repo); run `uv run python compare/generate.py` for the visuals.
"""

# ruff: noqa: E402 -- comparison cases are loaded from the adjacent upstream harness.

import sys
from pathlib import Path

import pytest

COMPARE = Path(__file__).resolve().parent.parent / "overlay" / "compare"
sys.path.insert(0, str(COMPARE))
from cases import CASES

from saitenka.app import dictdb as _dictdb
from saitenka.app.config import load_config

_cfg = load_config()
pytestmark = pytest.mark.skipif(
    not _cfg.get("dicts") or not _dictdb.default_db_path().exists(),
    reason="no imported dictionary DB (run `saitenka import` — see overlay.example.toml)",
)


@pytest.fixture(scope="module")
def dict_set():
    # This parity test deliberately runs against the USER'S real, already-imported dictionary DB —
    # opt out of conftest's per-test hermetic DB override so we read data_dir()/dictionaries.sqlite.
    from saitenka.app import dictdb
    from saitenka.app.dictionary import DictionarySet

    saved = dictdb._DB_PATH_OVERRIDE
    dictdb._DB_PATH_OVERRIDE = None
    try:
        db = dictdb.DictionaryDb.open()
        ds = DictionarySet.from_db(
            db, _cfg.get("dicts") or [], _cfg.get("freq") or [], _cfg.get("pitch") or []
        )
        if not ds.dicts:
            pytest.skip("configured dictionary titles not imported into the DB yet")
        yield ds
    finally:
        dictdb._DB_PATH_OVERRIDE = saved


@pytest.mark.parametrize("case", CASES, ids=[c["word"] for c in CASES])
def test_tooltip_parity(case, dict_set):
    from saitenka.app.tokenize import Token

    tok = Token(
        surface=case["surface"],
        lemma=case["lemma"],
        reading=case["reading"],
        pos=case["pos"],
        start=0,
        end=len(case["surface"]),
    )
    entry = dict_set.entry_for(tok)
    got = {f.name for f in entry.freqs}
    assert got, f"{case['word']}: no frequency pills rendered (have none)"
    assert entry.inflection_chain == case["expect_chain"], f"{case['word']}: wrong inflection chain"
    assert entry.defs and entry.defs[0].dict_name != "—", f"{case['word']}: no dictionary entry"
