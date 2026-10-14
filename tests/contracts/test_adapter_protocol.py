from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from . import cli_adapter, config_adapter
from .protocol import (
    PROTOCOL,
    assert_observations,
    load_scenarios,
    run_executable,
    run_protocol,
    scenario_request,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_non_python_adapter_obeys_framing_without_receiving_the_answer_key() -> None:
    scenario = {
        "protocol": PROTOCOL,
        "scenario_id": "protocol-smoke",
        "requires": ["session-v0"],
        "given": {},
        "when": [{"action": "checkpoint", "name": "ready"}],
        "expect": {
            "mode": "exact",
            "observations": [{"source": "driver", "kind": "checkpoint", "name": "ready"}],
        },
    }

    [reply] = run_executable([str(ROOT / "contracts/examples/session-v0-adapter.sh")], [scenario])

    assert reply["scenario_id"] == scenario["scenario_id"]
    assert_observations(scenario["expect"], reply["observations"])
    assert "expect" not in scenario_request(scenario)


def test_comparator_rejects_a_representative_broken_observation() -> None:
    expected = {
        "mode": "contains_in_order",
        "observations": [{"source": "clipboard", "kind": "write", "text": "new"}],
    }

    with pytest.raises(AssertionError, match="missing ordered observation"):
        assert_observations(
            expected,
            [{"source": "clipboard", "kind": "write", "text": "old"}],
        )


def test_contract_schemas_are_machine_readable() -> None:
    for name in ("session-v0.json", "config-v0.json", "cli-v0.json"):
        schema = json.loads((ROOT / "contracts/schema" / name).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_default_adapter_rejects_an_answer_key() -> None:
    scenario = load_scenarios(ROOT / "contracts/session/v0")[0]
    request = scenario_request(scenario) | {"expect": scenario["expect"]}
    wire = [
        {"type": "hello", "protocol": PROTOCOL, "requires": ["session-v0"]},
        request,
    ]
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("default_adapter.py"))],
        input="".join(json.dumps(row) + "\n" for row in wire),
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 2


def _json_rows(path: str) -> list[dict]:
    import json

    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@pytest.mark.integration
@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "corpus",
    ["session", "config", "cli"],
)
def test_default_adapter_runs_every_corpus_row_without_receiving_answers(corpus: str) -> None:
    if corpus == "session":
        scenarios = load_scenarios(ROOT / "contracts/session/v0")
        protocol, capability = PROTOCOL, "session-v0"
        requests = [scenario_request(row) for row in scenarios]
    elif corpus == "config":
        scenarios = [
            row
            for name in ("editor.json", "options.json", "resolution.json")
            for row in _json_rows(f"contracts/config/v0/{name}")
        ]
        protocol, capability = config_adapter.PROTOCOL, config_adapter.CAPABILITY
        requests = [config_adapter.scenario_request(row) for row in scenarios]
    else:
        scenarios = _json_rows("contracts/cli/v0/scenarios.json")
        protocol, capability = cli_adapter.PROTOCOL, cli_adapter.CAPABILITY
        requests = [cli_adapter.scenario_request(row) for row in scenarios]

    results = run_protocol(
        [sys.executable, str(Path(__file__).with_name("default_adapter.py"))],
        protocol=protocol,
        capability=capability,
        requests=requests,
    )

    for scenario, request, result in zip(scenarios, requests, results, strict=True):
        assert not {"expect", "expect_contains"} & request.keys()
        observations = result["observations"]
        if corpus == "session":
            assert_observations(scenario["expect"], observations)
        elif "expect_contains" in scenario:
            for key, expected in scenario["expect_contains"].items():
                assert set(expected) <= set(observations[key])
        else:
            assert observations == scenario["expect"]
