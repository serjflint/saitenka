from __future__ import annotations

from pathlib import Path

import pytest

from .protocol import assert_observations, load_scenarios, scenario_request
from .saitenka_session_adapter import execute

CORPUS = Path(__file__).resolve().parents[2] / "contracts/session/v0"
SCENARIOS = load_scenarios(CORPUS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["scenario_id"])
def test_session_contract(scenario: dict) -> None:
    reply = execute(scenario_request(scenario))

    assert reply["status"] == "ok"
    assert reply["scenario_id"] == scenario["scenario_id"]
    assert_observations(scenario["expect"], reply["observations"])
