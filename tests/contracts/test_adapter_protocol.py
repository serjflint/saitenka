from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from . import (
    analysis_adapter,
    cli_adapter,
    config_adapter,
    dictionary_adapter,
    history_adapter,
    picker_adapter,
    subtitle_adapter,
    tooltip_adapter,
    wordstate_adapter,
)
from .protocol import (
    PROTOCOL,
    assert_observations,
    load_row_corpus,
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
    corpora = {
        "session-v0.json": load_scenarios(ROOT / "contracts/session/v0"),
        "config-v0.json": [
            row
            for name in (
                "editor-session.json",
                "editor.json",
                "config-policy.json",
                "options.json",
                "profile-policy.json",
                "profile-switch.json",
                "profiles.json",
                "resolution.json",
            )
            for row in _json_rows(f"contracts/config/v0/{name}")
        ],
        "cli-v0.json": load_row_corpus(ROOT / "contracts/cli/v0"),
        "subtitle-v0.json": load_row_corpus(ROOT / "contracts/subtitle/v0"),
        "wordstate-v0.json": load_row_corpus(ROOT / "contracts/wordstate/v0"),
        "analysis-v0.json": load_row_corpus(ROOT / "contracts/analysis/v0"),
        "dictionary-v0.json": load_row_corpus(ROOT / "contracts/dictionary/v0"),
        "history-v0.json": load_row_corpus(ROOT / "contracts/history/v0"),
        "picker-v0.json": load_row_corpus(ROOT / "contracts/picker/v0"),
        "tooltip-v0.json": load_row_corpus(ROOT / "contracts/tooltip/v0"),
    }
    for name, rows in corpora.items():
        schema = json.loads((ROOT / "contracts/schema" / name).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for row in rows:
            validator.validate(row)
        with pytest.raises(ValidationError):
            validator.validate(rows[0] | {"unknown_protocol_key": True})


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
    [
        "session",
        "config",
        "cli",
        "subtitle",
        "wordstate",
        "analysis",
        "dictionary",
        "tooltip",
        "history",
        "picker",
    ],
)
def test_default_adapter_runs_every_corpus_row_without_receiving_answers(corpus: str) -> None:
    if corpus == "session":
        scenarios = load_scenarios(ROOT / "contracts/session/v0")
        protocol, capability = PROTOCOL, "session-v0"
        requests = [scenario_request(row) for row in scenarios]
    elif corpus == "config":
        scenarios = [
            row
            for name in (
                "editor-session.json",
                "editor.json",
                "config-policy.json",
                "options.json",
                "profile-policy.json",
                "profile-switch.json",
                "profiles.json",
                "resolution.json",
            )
            for row in _json_rows(f"contracts/config/v0/{name}")
        ]
        protocol, capability = config_adapter.PROTOCOL, config_adapter.CAPABILITY
        requests = [config_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "cli":
        scenarios = load_row_corpus(ROOT / "contracts/cli/v0")
        protocol, capability = cli_adapter.PROTOCOL, cli_adapter.CAPABILITY
        requests = [cli_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "subtitle":
        scenarios = load_row_corpus(ROOT / "contracts/subtitle/v0")
        protocol, capability = subtitle_adapter.PROTOCOL, subtitle_adapter.CAPABILITY
        requests = [subtitle_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "wordstate":
        scenarios = load_row_corpus(ROOT / "contracts/wordstate/v0")
        protocol, capability = wordstate_adapter.PROTOCOL, wordstate_adapter.CAPABILITY
        requests = [wordstate_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "analysis":
        scenarios = load_row_corpus(ROOT / "contracts/analysis/v0")
        protocol, capability = analysis_adapter.PROTOCOL, analysis_adapter.CAPABILITY
        requests = [analysis_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "dictionary":
        scenarios = load_row_corpus(ROOT / "contracts/dictionary/v0")
        protocol, capability = dictionary_adapter.PROTOCOL, dictionary_adapter.CAPABILITY
        requests = [dictionary_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "tooltip":
        scenarios = load_row_corpus(ROOT / "contracts/tooltip/v0")
        protocol, capability = tooltip_adapter.PROTOCOL, tooltip_adapter.CAPABILITY
        requests = [tooltip_adapter.scenario_request(row) for row in scenarios]
    elif corpus == "history":
        scenarios = load_row_corpus(ROOT / "contracts/history/v0")
        protocol, capability = history_adapter.PROTOCOL, history_adapter.CAPABILITY
        requests = [history_adapter.scenario_request(row) for row in scenarios]
    else:
        scenarios = load_row_corpus(ROOT / "contracts/picker/v0")
        protocol, capability = picker_adapter.PROTOCOL, picker_adapter.CAPABILITY
        requests = [picker_adapter.scenario_request(row) for row in scenarios]

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
