from __future__ import annotations

import json
from pathlib import Path

import pytest

from .cli_adapter import evaluate
from .protocol import scenario_key_hash

CORPUS = Path(__file__).resolve().parents[2] / "contracts/cli/v0"
SCENARIOS = json.loads((CORPUS / "scenarios.json").read_text(encoding="utf-8"))


def _locked() -> list[dict]:
    ids = [scenario["scenario_id"] for scenario in SCENARIOS]
    manifest = json.loads((CORPUS / "manifest.lock").read_text(encoding="utf-8"))
    assert manifest == {"count": len(ids), "key_sha256": scenario_key_hash(ids)}
    return SCENARIOS


@pytest.mark.parametrize("scenario", _locked(), ids=lambda row: row["scenario_id"])
def test_cli_contract(scenario: dict) -> None:
    actual = evaluate(scenario)
    if "expect_contains" in scenario:
        for key, expected in scenario["expect_contains"].items():
            assert set(expected) <= set(actual[key])
    else:
        assert actual == scenario["expect"]
