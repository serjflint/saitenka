"""Report declared diagnostic evidence availability, not line coverage or fault-detection proof."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diagnostic_findings import diagnose, read_envelope

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


def coverage(events: list[dict], scenarios: list[dict]) -> dict:
    rows = []
    for scenario in scenarios:
        requirements = scenario["evidence"]
        satisfied = []
        identities = []
        join = scenario.get("join")
        for requirement in requirements:
            matching = [event for event in events if matches(event, requirement)]
            satisfied.append(bool(matching))
            if join:
                identities.append(
                    {
                        str(event["args"][join])
                        for event in matching
                        if isinstance(event["args"].get(join), (str, int))
                    }
                )
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
                "join": join,
                "joined_operations": len(joined) if join else None,
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
        "--require", action="append", default=[], help="fail if this scenario lacks evidence"
    )
    args = parser.parse_args()
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    envelope = read_envelope(args.report) if args.report.suffix != ".json" else {}
    trace = trace_evidence(args.report)
    result = coverage(trace.pop("events"), inventory["scenarios"])
    result["diagnosis"] = diagnose(envelope)
    result["trace_evidence"] = trace
    print(json.dumps(result, indent=2))
    qualified = {row["id"] for row in result["scenarios"] if row["status"] == "observed"}
    return 1 if set(args.require) - qualified else 0


if __name__ == "__main__":
    raise SystemExit(main())
