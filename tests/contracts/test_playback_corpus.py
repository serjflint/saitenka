from __future__ import annotations

from pathlib import Path

import pytest

from .playback_adapter import evaluate
from .protocol import load_row_corpus

CORPUS = Path(__file__).resolve().parents[2] / "contracts/playback/v0"
SCENARIOS = load_row_corpus(CORPUS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["scenario_id"])
def test_playback_contract(scenario: dict) -> None:
    assert evaluate(scenario) == scenario["expect"]
