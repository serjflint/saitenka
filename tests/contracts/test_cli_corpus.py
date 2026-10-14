from __future__ import annotations

from pathlib import Path

import pytest

from .cli_adapter import evaluate
from .protocol import load_row_corpus

CORPUS = Path(__file__).resolve().parents[2] / "contracts/cli/v0"
SCENARIOS = load_row_corpus(CORPUS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["scenario_id"])
def test_cli_contract(scenario: dict) -> None:
    actual = evaluate(scenario)
    if "expect_contains" in scenario:
        for key, expected in scenario["expect_contains"].items():
            assert set(expected) <= set(actual[key])
    else:
        assert actual == scenario["expect"]
