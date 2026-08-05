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
from saitenka_deinflect import condition_flags, conditions_match, deinflect

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "japanese_transforms_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


def _has_term_reasons(
    source: str, term: str, rule: str | None, reasons: list[str]
) -> bool:
    """Port of Yomitan's fixture ``hasTermReasons``: does any deinflection of ``source`` reach
    ``term`` with matching POS conditions and the exact transform chain?"""
    rule_flags = None if rule is None else condition_flags(rule)
    for d in deinflect(source):
        if d.text != term:
            continue
        if rule_flags is not None and not conditions_match(d.conditions, rule_flags):
            continue
        if list(d.chain) == reasons:
            return True
    return False


@pytest.mark.parametrize(
    "case",
    _CASES,
    ids=[f"{c['category']}:{c['source']}->{c['term']}" for c in _CASES],
)
def test_matches_yomitan_transform_corpus(case):
    got = _has_term_reasons(case["source"], case["term"], case["rule"], case["reasons"])
    assert got is case["valid"]
