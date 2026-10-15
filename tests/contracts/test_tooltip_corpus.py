from __future__ import annotations

from pathlib import Path

import pytest

from .protocol import load_row_corpus
from .tooltip_adapter import evaluate, scenario_request

CORPUS = Path(__file__).resolve().parents[2] / "contracts/tooltip/v0"
SCENARIOS = load_row_corpus(CORPUS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["scenario_id"])
def test_tooltip_contract(scenario: dict) -> None:
    assert evaluate(scenario_request(scenario)) == scenario["expect"]
