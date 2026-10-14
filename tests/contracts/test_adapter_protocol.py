from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .protocol import (
    PROTOCOL,
    assert_observations,
    load_scenarios,
    run_executable,
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
        "when": [{"action": "show", "text": "ready"}],
        "expect": {
            "mode": "exact",
            "observations": [{"source": "mpv", "kind": "command", "value": ["show-text", "ready"]}],
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


@pytest.mark.integration
@pytest.mark.timeout(10)
def test_default_adapter_runs_the_same_corpus_through_the_executable_port() -> None:
    scenarios = load_scenarios(ROOT / "contracts/session/v0")
    scenario = next(row for row in scenarios if row["scenario_id"] == "startup-observes-playback")

    [reply] = run_executable(
        [sys.executable, str(Path(__file__).with_name("default_adapter.py"))],
        [scenario],
    )

    assert_observations(scenario["expect"], reply["observations"])
