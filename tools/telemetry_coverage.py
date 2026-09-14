"""Report declared diagnostic evidence availability, not line coverage or fault-detection proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from character_masks import qualification_counts
from diagnostic_findings import diagnose_report, evaluate_fault

from saitenka.app.report_reader import trace_evidence

INVENTORY = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "telemetry-scenarios.json"


def matches(event: dict, requirement: dict) -> bool:
    args = event.get("args", {})
    return (
        event.get("ph") == "X"
        and event.get("name") == requirement["name"]
        and isinstance(args, dict)
        and all(key in args for key in requirement.get("fields", []))
        and all(args.get(key) in values for key, values in requirement.get("values", {}).items())
    )


def _identity(value: object) -> bool:
    return (type(value) is int and value >= 0) or (isinstance(value, str) and bool(value))


def _causal_counts(identities: list[set[str]], missing: int, malformed: int) -> dict:
    started = identities[0]
    complete = set.intersection(*identities)
    observed = set.union(*identities)
    return {
        "scope": "declared joined boundaries; first requirement is the start, not pixel proof",
        "started_operations": len(started),
        "complete_operations": len(complete),
        "incomplete_operations": len(started - complete),
        "orphan_operations": len(observed - started),
        "missing_identity_events": missing,
        "invalid_schema_events": malformed,
    }


def coverage(events: list[dict], scenarios: list[dict]) -> dict:
    rows = []
    for scenario in scenarios:
        requirements = scenario["evidence"]
        satisfied = []
        identities = []
        join = scenario.get("join")
        missing = 0
        malformed = 0
        for requirement in requirements:
            matching = [event for event in events if matches(event, requirement)]
            malformed += sum(
                event.get("ph") == "X"
                and event.get("name") == requirement["name"]
                and not matches(event, requirement)
                for event in events
            )
            satisfied.append(bool(matching))
            if join:
                identities.append(
                    {
                        f"{type(event['args'][join]).__name__}:{event['args'][join]}"
                        for event in matching
                        if _identity(event["args"].get(join))
                    }
                )
                missing += sum(not _identity(event["args"].get(join)) for event in matching)
        joined = set.intersection(*identities) if identities else set()
        complete = bool(requirements and all(satisfied) and (not join or joined))
        rows.append(
            {
                "id": scenario["id"],
                "owner": scenario["owner"],
                "critical": scenario["critical"],
                "observed": sum(satisfied),
                "required": len(requirements),
                "status": "observed" if complete else "partial" if any(satisfied) else "unobserved",
                "qualification": "not established by presence alone",
                "missing_evidence_contract": scenario.get("diagnostic_contract")
                if not complete
                else None,
                "join": join,
                "joined_operations": len(joined) if join else None,
                "causal_completeness": _causal_counts(identities, missing, malformed)
                if identities
                else {"status": "unknown", "reason": "no-declared-operation-join"},
                "tests": scenario["tests"],
            }
        )
    return {
        "scope": "declared scenario evidence availability; not whole-product qualification",
        "observed": sum(row["status"] == "observed" for row in rows),
        "declared": len(rows),
        "scenarios": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--fault-case", help="validate a caller-declared injected fault from the scenario inventory"
    )
    parser.add_argument(
        "--character-corpus",
        type=Path,
        action="append",
        default=[],
        help="local directory with pinned manifest.json and results.json; accounted separately",
    )
    parser.add_argument(
        "--require", action="append", default=[], help="fail if this scenario lacks evidence"
    )
    parser.add_argument(
        "--require-complete",
        action="append",
        default=[],
        help="require joined starts with no missing terminal, orphan or unidentified event",
    )
    parser.add_argument(
        "--require-character-qualification",
        action="store_true",
        help="require at least one supplied corpus and every required character to qualify",
    )
    args = parser.parse_args()
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    trace = trace_evidence(args.report)
    result = coverage(trace.pop("events"), inventory["scenarios"])
    result["bridge_contracts"] = {
        "declared": len(inventory.get("bridge_cases", [])),
        "cases": inventory.get("bridge_cases", []),
        "execution": "unknown; static test references are not a pytest execution receipt",
    }
    result["diagnosis"] = diagnose_report(args.report, trace=trace)
    result["trace_evidence"] = trace
    faults = {
        case["id"]: {**case, "required_evidence": row["fault_evidence"]}
        for row in inventory["scenarios"]
        for case in row.get("fault_cases", [])
    }
    if args.fault_case is not None:
        result["fault_detection"] = (
            evaluate_fault(result["diagnosis"], faults[args.fault_case])
            if args.fault_case in faults
            else {"passed": False, "reason": "unknown-fault-case"}
        )
    result["fault_coverage"] = {
        "declared_cases": len(faults),
        "evaluated_cases": int(args.fault_case in faults),
        "matching_cases": int(result.get("fault_detection", {}).get("passed") is True),
        "scope": "explicitly declared injected case only; test references are not execution evidence",
    }
    result["character_corpora"] = [_character_corpus(path) for path in args.character_corpus]
    print(json.dumps(result, indent=2))
    qualified = {row["id"] for row in result["scenarios"] if row["status"] == "observed"}
    capture_complete = trace["status"] == "readable" and not any(
        row["category"] == "trace-incomplete" for row in result["diagnosis"]["findings"]
    )
    complete = {
        row["id"] for row in result["scenarios"] if capture_complete and causally_complete(row)
    }
    characters = result["character_corpora"]
    failed = (
        result.get("fault_detection", {}).get("passed") is False
        or bool(set(args.require) - qualified)
        or bool(set(args.require_complete) - complete)
        or (
            args.require_character_qualification
            and (not characters or not all(row.get("qualified") is True for row in characters))
        )
    )
    return int(failed)


def causally_complete(row: dict) -> bool:
    counts = row["causal_completeness"]
    return (
        counts.get("started_operations", 0) > 0
        and counts.get("incomplete_operations") == 0
        and counts.get("orphan_operations") == 0
        and counts.get("missing_identity_events") == 0
        and counts.get("invalid_schema_events") == 0
    )


def _character_corpus(directory: Path) -> dict:
    def read(name: str) -> dict:
        with (directory / name).open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("oversized character artifact")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("invalid character artifact")
        return value

    try:
        return qualification_counts(read("manifest.json"), read("results.json"))
    except (OSError, ValueError, TypeError, RecursionError):
        return {
            "status": "invalid",
            "reason": "missing, malformed, oversized or mismatched character artifacts",
        }


if __name__ == "__main__":
    raise SystemExit(main())
