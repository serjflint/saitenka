"""The external oracle: Yomitan's own transform conformance corpus.

`engine.py` ports Yomitan's `language-transformer.js`; this asserts the port against Yomitan's
`japanese-transforms.test.js` vectors (vendored + transcribed by `tools/gen_yomitan_cases.py`). Each
vector says `deinflect(source)` must (`valid`) or must not yield a candidate reaching `term` whose
condition flags match `rule` and whose transform chain equals `reasons` — a faithful port of
Yomitan's `hasTermReasons` harness, so passing proves upstream parity, not just our own grammar read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _corpus_oracle import has_term_reasons

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "japanese_transforms_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize(
    "case",
    _CASES,
    ids=[f"{c['category']}:{c['source']}->{c['term']}" for c in _CASES],
)
def test_matches_yomitan_transform_corpus(case):
    got = has_term_reasons(case["source"], case["term"], case["rule"], case["reasons"])
    assert got is case["valid"]
