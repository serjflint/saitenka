from __future__ import annotations

import json
from pathlib import Path

import pytest

from .config_adapter import evaluate
from .protocol import scenario_key_hash

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
