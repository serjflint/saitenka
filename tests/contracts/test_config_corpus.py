from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from .config_adapter import CAPABILITY, PROTOCOL, evaluate
from .protocol import run_protocol, scenario_key_hash

CORPUS = Path(__file__).resolve().parents[2] / "contracts/config/v0"
SCENARIOS = json.loads((CORPUS / "options.json").read_text(encoding="utf-8"))


def _locked() -> list[dict]:
    ids = [scenario["scenario_id"] for scenario in SCENARIOS]
    manifest = json.loads((CORPUS / "manifest.lock").read_text(encoding="utf-8"))
    assert manifest == {"count": len(ids), "key_sha256": scenario_key_hash(ids)}
    return SCENARIOS


@pytest.mark.parametrize("scenario", _locked(), ids=lambda row: row["scenario_id"])
def test_effective_options_contract(scenario: dict) -> None:
    assert evaluate(scenario) == scenario["expect"]


@pytest.mark.integration
@pytest.mark.timeout(10)
def test_config_adapter_runs_the_corpus_without_receiving_expected_answers() -> None:
    scenario = SCENARIOS[0]
    request = {key: value for key, value in scenario.items() if key != "expect"}
    request["observe"] = list(scenario["expect"])
    request.update(type="run", protocol=PROTOCOL)
    [result] = run_protocol(
        [sys.executable, str(Path(__file__).with_name("config_adapter.py"))],
        protocol=PROTOCOL,
        capability=CAPABILITY,
        requests=[request],
    )

    assert result["observations"] == scenario["expect"]
