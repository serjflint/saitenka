"""The external oracle for a language WITHOUT an upstream test suite: a differential corpus.

French ships no `french-transforms.test.js`, so the "steal the vectors" move (`test_yomitan_transform_corpus.py`)
can't cover it — French launched with 5 live bugs and zero conformance vectors. Instead the vectors are
*generated* by running Yomitan's real `LanguageTransformer` over a seed corpus (`tools/gen_transform_differential.mjs`),
then committed. Because `engine.py` + `french_transforms.json` derive from the SAME upstream, agreement is
expected by construction — so a red row here is a pure defect signal: an `engine.py` port bug, or a
lossy-dump bug (`dump_french_transforms.mjs` approximates `wholeWord` rules as `rule.deinflect('')`).

Hermetic (reads committed JSON; no Node in `poe all`), and asserts through the SAME `has_term_reasons`
port as the JP corpus — the sole differences are `language='fr'` and how the vectors were sourced.

This is the RULE-DATA layer only. Elision / decapitalization / apostrophe are preprocessor concerns
pinned end-to-end by the pipeline oracle (`overlay/tests/test_pipeline_oracle.py`) — a complementary layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _corpus_oracle import has_term_reasons

_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "french_transforms_cases.json").read_text(
        encoding="utf-8"
    )
)
_LANG = _CORPUS["language"]
_CASES = _CORPUS["cases"]


@pytest.mark.parametrize(
    "case",
    _CASES,
    ids=[f"{c['category']}:{c['source']}->{c['term']}" for c in _CASES],
)
def test_matches_yomitan_differential(case):
    got = has_term_reasons(
        case["source"], case["term"], case["rule"], case["reasons"], language=_LANG
    )
    assert got is case["valid"]


def test_pins_the_rule_data_live_bugs():
    """The two RULE-DATA regressions from the French launch resolve to their lemma (decap/elision pins
    are the pipeline oracle's job). A floor independent of the generated corpus's row ordering."""
    assert has_term_reasons("parapluies", "parapluie", None, ["plural"], language="fr")
    assert has_term_reasons("craint", "craindre", None, ["present indicative"], language="fr")


def test_the_differential_oracle_has_teeth():
    """Negative control: the oracle must FIRE on a wrong expectation, else a green run proves nothing.
    A real vector holds; a bogus term and a bogus reason-chain for a real source both fail."""
    assert has_term_reasons("craint", "craindre", None, ["present indicative"], language="fr")
    assert not has_term_reasons("craint", "zzqx", None, ["present indicative"], language="fr")
    assert not has_term_reasons("craint", "craindre", None, ["not a real reason"], language="fr")
