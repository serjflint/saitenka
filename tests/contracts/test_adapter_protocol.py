from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .protocol import (
    CORPORA,
    PROTOCOL,
    assert_observations,
    load_row_corpus,
    load_scenarios,
    run_executable,
    run_protocol,
    scenario_request,
)

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"


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

    [reply] = run_executable([str(HERE / "examples/session-v0-adapter.sh")], [scenario])

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


def test_manifest_rejects_removing_a_nested_case_with_its_answer(tmp_path: Path) -> None:
    source = CORPUS / "tooltip-v0.jsonl"
    shutil.copy(CORPUS / "manifest.json", tmp_path / "manifest.json")
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    rows[0]["cases"].pop()
    rows[0]["expect"].pop()
    changed = tmp_path / source.name
    changed.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(AssertionError):
        load_row_corpus(changed)


def test_manifest_rejects_removing_an_expected_observation(tmp_path: Path) -> None:
    source = CORPUS / "journey-v0.jsonl"
    shutil.copy(CORPUS / "manifest.json", tmp_path / "manifest.json")
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    rows[0]["expect"].pop()
    changed = tmp_path / source.name
    changed.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(AssertionError):
        load_row_corpus(changed)


def test_runner_rejects_a_result_for_the_wrong_scenario() -> None:
    program = """
import json, sys
hello = json.loads(sys.stdin.readline())
request = json.loads(sys.stdin.readline())
print(json.dumps({'type': 'hello', 'protocol': hello['protocol'], 'capabilities': hello['requires']}))
print(json.dumps({'type': 'result', 'protocol': request['protocol'], 'scenario_id': 'wrong', 'status': 'ok', 'observations': []}))
"""
    request = {
        "type": "run",
        "protocol": PROTOCOL,
        "scenario_id": "right",
        "requires": ["session-v0"],
        "given": {},
        "when": [],
    }

    with pytest.raises(AssertionError):
        run_protocol(
            [sys.executable, "-c", program],
            protocol=PROTOCOL,
            capability="session-v0",
            requests=[request],
        )


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_default_adapter_rejects_an_answer_key() -> None:
    scenario = load_scenarios(CORPUS / "session-v0.jsonl")[0]
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


@pytest.mark.integration
@pytest.mark.timeout(10)
@pytest.mark.parametrize("corpus", CORPORA)
def test_default_adapter_runs_every_corpus_row_without_receiving_answers(corpus: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("protocol.py")),
            "--corpus",
            corpus,
            "--",
            sys.executable,
            str(Path(__file__).with_name("default_adapter.py")),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
