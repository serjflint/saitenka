from __future__ import annotations

import hashlib
import json
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

PROTOCOL = "saitenka.session/v0"
CAPABILITY = "session-v0"
SCENARIO_KEYS = frozenset({"protocol", "scenario_id", "requires", "given", "when", "expect"})
EXPECT_KEYS = frozenset({"mode", "observations", "within_steps", "within_ms"})
MODES = frozenset({"exact", "contains_in_order", "eventually"})


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def scenario_request(scenario: dict) -> dict:
    validate_scenario(scenario)
    return {
        "type": "run",
        "protocol": scenario["protocol"],
        "scenario_id": scenario["scenario_id"],
        "requires": scenario["requires"],
        "given": scenario["given"],
        "when": scenario["when"],
    }


def validate_scenario(scenario: dict) -> None:
    if set(scenario) != SCENARIO_KEYS:
        raise ValueError(f"scenario keys must be {sorted(SCENARIO_KEYS)}")
    if scenario["protocol"] != PROTOCOL:
        raise ValueError(f"unsupported protocol: {scenario['protocol']!r}")
    if not isinstance(scenario["scenario_id"], str) or not scenario["scenario_id"]:
        raise ValueError("scenario_id must be a nonempty string")
    if scenario["requires"] != [CAPABILITY]:
        raise ValueError(f"session v0 requires exactly {CAPABILITY!r}")
    if not isinstance(scenario["given"], dict):
        raise TypeError("given must be an object")
    if not isinstance(scenario["when"], list) or any(
        not isinstance(action, dict) or not isinstance(action.get("action"), str)
        for action in scenario["when"]
    ):
        raise ValueError("when must be an array of actions")
    expect = scenario["expect"]
    if not isinstance(expect, dict) or not set(expect) <= EXPECT_KEYS:
        raise ValueError(f"expect keys must be drawn from {sorted(EXPECT_KEYS)}")
    if expect.get("mode") not in MODES or not isinstance(expect.get("observations"), list):
        raise ValueError("expect requires a known mode and an observations array")
    if expect["mode"] == "eventually" and not {
        "within_steps",
        "within_ms",
    } & set(expect):
        raise ValueError("eventually requires a logical-time or step bound")


def assert_observations(expect: dict, actual: Sequence[dict]) -> None:
    wanted = expect["observations"]
    mode = expect["mode"]
    if mode == "exact":
        assert list(actual) == wanted, f"expected {wanted!r}, got {list(actual)!r}"
        return
    if mode == "contains_in_order":
        remaining = iter(actual)
        for observation in wanted:
            if not any(candidate == observation for candidate in remaining):
                raise AssertionError(
                    f"missing ordered observation {observation!r} in {list(actual)!r}"
                )
        return
    assert mode == "eventually"
    assert len(wanted) == 1, "eventually v0 accepts one bounded observation"
    assert wanted[0] in actual


def run_executable(command: Sequence[str], scenarios: Iterable[dict]) -> list[dict]:
    scenario_list = list(scenarios)
    return run_protocol(
        command,
        protocol=PROTOCOL,
        capability=CAPABILITY,
        requests=[scenario_request(scenario) for scenario in scenario_list],
    )


def run_protocol(
    command: Sequence[str], *, protocol: str, capability: str, requests: Sequence[dict]
) -> list[dict]:
    wire = [{"type": "hello", "protocol": protocol, "requires": [capability]}, *requests]
    completed = subprocess.run(
        command,
        input="".join(f"{compact_json(request)}\n" for request in wire),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert replies[0] == {
        "type": "hello",
        "protocol": protocol,
        "capabilities": [capability],
    }
    assert len(replies) == len(wire)
    return replies[1:]


def scenario_key_hash(ids: Iterable[str]) -> str:
    payload = "\n".join(sorted(ids)).encode()
    return hashlib.sha256(payload).hexdigest()


def load_scenarios(directory: Path) -> list[dict]:
    scenarios = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))
    ]
    for scenario in scenarios:
        validate_scenario(scenario)
    ids = [scenario["scenario_id"] for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario IDs must be unique")
    manifest = json.loads((directory / "manifest.lock").read_text(encoding="utf-8"))
    assert manifest == {"count": len(ids), "key_sha256": scenario_key_hash(ids)}
    return scenarios
