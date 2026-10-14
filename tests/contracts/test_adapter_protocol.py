from __future__ import annotations

import sys
from pathlib import Path

import pytest

from . import cli_adapter, config_adapter, playback_adapter
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


def _json_rows(path: str) -> list[dict]:
    import json

    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _adapter_cases() -> list[tuple[str, str, dict, object]]:
    session = next(
        row
        for row in load_scenarios(ROOT / "contracts/session/v0")
        if row["scenario_id"] == "startup-observes-playback"
    )
    config = next(
        row for row in _json_rows("contracts/config/v0/options.json") if row["mode"] == "run"
    )
    playback = _json_rows("contracts/playback/v0/scenarios.json")[0]
    cli = next(row for row in _json_rows("contracts/cli/v0/scenarios.json") if "expect" in row)
    config_request = {key: value for key, value in config.items() if key != "expect"}
    config_request["observe"] = list(config["expect"])
    return [
        (PROTOCOL, "session-v0", scenario_request(session), session["expect"]),
        (
            config_adapter.PROTOCOL,
            config_adapter.CAPABILITY,
            {"type": "run", "protocol": config_adapter.PROTOCOL, **config_request},
            config["expect"],
        ),
        (
            playback_adapter.PROTOCOL,
            playback_adapter.CAPABILITY,
            {
                "type": "run",
                "protocol": playback_adapter.PROTOCOL,
                **{key: value for key, value in playback.items() if key != "expect"},
            },
            playback["expect"],
        ),
        (
            cli_adapter.PROTOCOL,
            cli_adapter.CAPABILITY,
            {
                "type": "run",
                "protocol": cli_adapter.PROTOCOL,
                **{key: value for key, value in cli.items() if key != "expect"},
            },
            cli["expect"],
        ),
    ]


@pytest.mark.integration
@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    ("protocol", "capability", "payload", "expected"),
    _adapter_cases(),
    ids=("session", "config", "playback", "cli"),
)
def test_default_adapter_runs_each_corpus_without_receiving_answers(
    protocol: str, capability: str, payload: dict, expected: object
) -> None:
    [result] = run_protocol(
        [sys.executable, str(Path(__file__).with_name("default_adapter.py"))],
        protocol=protocol,
        capability=capability,
        requests=[payload],
    )

    observations = result["observations"]
    if protocol == PROTOCOL:
        assert_observations(expected, observations)
    else:
        assert observations == expected
