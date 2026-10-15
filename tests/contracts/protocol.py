from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

PROTOCOL = "saitenka.session/v0"
CAPABILITY = "session-v0"
CORPORA = {
    "analysis": ("saitenka.analysis/v0", "analysis-v0"),
    "cli": ("saitenka.cli/v0", "cli-v0"),
    "config": ("saitenka.config/v0", "config-v0"),
    "dictionary": ("saitenka.dictionary/v0", "dictionary-v0"),
    "history": ("saitenka.history/v0", "history-v0"),
    "journey": ("saitenka.journey/v0", "journey-v0"),
    "picker": ("saitenka.picker/v0", "picker-v0"),
    "session": (PROTOCOL, CAPABILITY),
    "sidebar": ("saitenka.sidebar/v0", "sidebar-v0"),
    "subtitle": ("saitenka.subtitle/v0", "subtitle-v0"),
    "tooltip": ("saitenka.tooltip/v0", "tooltip-v0"),
    "wordstate": ("saitenka.wordstate/v0", "wordstate-v0"),
}
SCENARIO_KEYS = frozenset({"protocol", "scenario_id", "requires", "given", "when", "expect"})
EXPECT_KEYS = frozenset({"mode", "observations"})
MODES = frozenset({"exact", "contains_in_order"})
ACTION_KEYS = {
    "start": frozenset({"action"}),
    "emit_batch": frozenset({"action", "events"}),
    "command": frozenset({"action", "name"}),
    "transport_lost": frozenset({"action", "epoch"}),
    "transport_ready": frozenset({"action", "epoch"}),
    "transport_replaced": frozenset({"action", "epoch"}),
    "drain_until_quiescent": frozenset({"action", "max_steps"}),
    "close": frozenset({"action"}),
    "checkpoint": frozenset({"action", "name"}),
    "await_notes": frozenset({"action", "count", "max_steps"}),
}


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


def validate_request(request: dict) -> None:
    expected = {"type", "protocol", "scenario_id", "requires", "given", "when"}
    if set(request) != expected or request.get("type") != "run":
        raise ValueError("invalid session-v0 request envelope")
    validate_scenario(
        {key: value for key, value in request.items() if key != "type"}
        | {"expect": {"mode": "exact", "observations": []}}
    )


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
    for action in scenario["when"]:
        allowed = ACTION_KEYS.get(action["action"])
        if allowed is None or set(action) != allowed:
            raise ValueError(f"invalid {action['action']!r} action keys")
    expect = scenario["expect"]
    if not isinstance(expect, dict) or not set(expect) <= EXPECT_KEYS:
        raise ValueError(f"expect keys must be drawn from {sorted(EXPECT_KEYS)}")
    if expect.get("mode") not in MODES or not isinstance(expect.get("observations"), list):
        raise ValueError("expect requires a known mode and an observations array")


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
    raise AssertionError(f"unsupported comparison mode: {mode!r}")


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
    results = replies[1:]
    for request, result in zip(requests, results, strict=True):
        assert result.get("type") == "result"
        assert result.get("protocol") == protocol
        assert result.get("scenario_id") == request["scenario_id"]
        assert result.get("status") == "ok"
        assert "observations" in result
    return results


def scenario_key_hash(ids: Iterable[str]) -> str:
    payload = "\n".join(sorted(ids)).encode()
    return hashlib.sha256(payload).hexdigest()


def _request_payload(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in {"expect", "expect_contains"}}


def _manifest(rows: Sequence[dict]) -> dict:
    ids = [row["scenario_id"] for row in rows]
    vector_ids = [
        hashlib.sha256(compact_json(case).encode()).hexdigest()
        for row in rows
        for case in row.get("cases", ())
    ]
    input_payload = "\n".join(compact_json(_request_payload(row)) for row in rows).encode()
    oracle_payload = "\n".join(
        compact_json({key: row[key] for key in ("expect", "expect_contains") if key in row})
        for row in rows
    ).encode()
    oracle_count = sum(
        len(value) if isinstance(value := row.get("expect"), list) else 1 for row in rows
    )
    return {
        "count": len(ids),
        "key_sha256": scenario_key_hash(ids),
        "vector_count": len(vector_ids) or len(ids),
        "vector_key_sha256": scenario_key_hash(vector_ids or ids),
        "input_sha256": hashlib.sha256(input_payload).hexdigest(),
        "oracle_count": oracle_count,
        "oracle_sha256": hashlib.sha256(oracle_payload).hexdigest(),
    }


def expected_manifest(rows: Sequence[dict]) -> dict:
    """Return the deliberate-rebless value for a corpus directory."""
    return _manifest(rows)


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
    assert manifest == _manifest(scenarios)
    return scenarios


def load_row_corpus(directory: Path) -> list[dict]:
    rows = [
        row
        for path in sorted(directory.glob("*.json"))
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]
    ids = [row["scenario_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario IDs must be unique")
    for row in rows:
        cases = row.get("cases")
        if cases is None:
            continue
        expected = row.get("expect")
        if not isinstance(expected, list) or len(cases) != len(expected):
            raise ValueError(f"{row['scenario_id']}: cases and expect must have equal length")
    manifest = json.loads((directory / "manifest.lock").read_text(encoding="utf-8"))
    assert manifest == _manifest(rows)
    return rows


def run_corpus(
    command: Sequence[str],
    corpus: str,
    *,
    root: Path | None = None,
    scenario_id: str | None = None,
) -> None:
    """Judge one committed corpus against any JSONL adapter executable."""
    base = root or Path(__file__).resolve().parents[2]
    protocol, capability = CORPORA[corpus]
    directory = base / "contracts" / corpus / "v0"
    scenarios = load_scenarios(directory) if corpus == "session" else load_row_corpus(directory)
    if scenario_id is not None:
        scenarios = [scenario for scenario in scenarios if scenario["scenario_id"] == scenario_id]
        if not scenarios:
            raise ValueError(f"unknown {corpus} scenario: {scenario_id}")
    requests = [
        {"type": "run", "protocol": protocol, **_request_payload(scenario)}
        for scenario in scenarios
    ]
    results = run_protocol(command, protocol=protocol, capability=capability, requests=requests)
    for scenario, result in zip(scenarios, results, strict=True):
        try:
            actual = result["observations"]
            if corpus == "session":
                assert_observations(scenario["expect"], actual)
            elif "expect_contains" in scenario:
                for key, expected in scenario["expect_contains"].items():
                    assert set(expected) <= set(actual[key])
            else:
                assert actual == scenario["expect"]
        except (AssertionError, KeyError) as exc:
            exc.add_note(f"scenario_id={scenario['scenario_id']}")
            raise


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Judge a Saitenka compatibility corpus")
    parser.add_argument("--corpus", choices=sorted(CORPORA), required=True)
    parser.add_argument("--scenario", help="run one scenario by its stable id")
    parser.add_argument("adapter", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.adapter)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("adapter command is required after --")
    run_corpus(command, args.corpus, scenario_id=args.scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
