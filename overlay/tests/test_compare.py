"""Parity check: our tooltip vs SubMiner for the same episode words.

Structural (not pixel) comparison — asserts our render carries the same frequency dictionaries and
the same inflection chain SubMiner shows. Skipped unless the configured dictionaries are present
(they're large and live outside the repo); run `uv run python compare/generate.py` for the visuals.
"""

import sys
from pathlib import Path

import pytest

COMPARE = Path(__file__).resolve().parent.parent / "compare"
sys.path.insert(0, str(COMPARE))
from cases import CASES  # noqa: E402

from overlay.app.config import expand_paths, load_config  # noqa: E402

_cfg = load_config()
_dicts = expand_paths(_cfg.get("dicts")) if _cfg else []
pytestmark = pytest.mark.skipif(
    not _dicts or not all(Path(p).exists() for p in _dicts),
    reason="configured dictionaries not present (see overlay.example.toml)",
)


@pytest.fixture(scope="module")
def dict_set():
    # This parity test deliberately runs against the USER'S real dict set — reuse the real cache
    # dir (conftest hermetically redirects it for every other test), else it would rebuild
    # gigabytes into a tmp dir on every session.
    import overlay.app.dictionary as _dictionary
    from overlay.app.dictionary import CACHE_DIR as _test_cache
    from overlay.app.dictionary import DictionarySet

    _dictionary.CACHE_DIR = _dictionary._REAL_CACHE_DIR
    try:
        yield DictionarySet.load(
            _dicts,
            freq_paths=expand_paths(_cfg.get("freq")),
            pitch_paths=expand_paths(_cfg.get("pitch")),
        )
    finally:
        _dictionary.CACHE_DIR = _test_cache


@pytest.mark.parametrize("case", CASES, ids=[c["word"] for c in CASES])
def test_tooltip_parity(case, dict_set):
    from overlay.app.tokenize import Token

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
