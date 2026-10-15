from __future__ import annotations

from pathlib import Path

import pytest

from .protocol import load_row_corpus
from .wordstate_adapter import evaluate, scenario_request

CORPUS = Path(__file__).resolve().parents[2] / "contracts/wordstate/v0"
SCENARIOS = load_row_corpus(CORPUS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["scenario_id"])
def test_wordstate_contract(scenario: dict) -> None:
    actual = evaluate(scenario_request(scenario))
    for got, expected in zip(actual, scenario["expect"], strict=True):
        if isinstance(expected, float):
            assert got == pytest.approx(expected, abs=1e-9)
        else:
            assert got == expected
