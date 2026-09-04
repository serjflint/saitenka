"""Shared Sharpen disposition policy and generated Workflow evaluator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / ".agents/sharpen/policy.json"
HARNESS_PATH = ROOT / ".agents/sharpen/harness.js"
BEGIN = "// BEGIN GENERATED SHARPEN POLICY — tools/sharpen_policy.py sync"
END = "// END GENERATED SHARPEN POLICY"


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("version") != 1 or set(policy.get("modes", {})) != {
        "efficacy",
        "conformance",
    }:
        raise ValueError("unsupported Sharpen policy")
    return policy


def gate_passes(mode: str, gate: dict, policy: dict | None = None) -> bool:
    current = policy or load_policy()
    active = current["modes"].get(mode)
    if not isinstance(active, dict):
        return False
    return bool(
        all(gate.get(field) is True for field in active["gate_true"])
        and all(gate.get(field) is None for field in active["gate_null"])
        and all(gate.get(field) is not False for field in current["gate_not_false"])
    )


def _axis_sets(record: dict, policy: dict) -> tuple[set[str], set[str]] | None:
    axes = record.get("axes")
    skipped = record.get("axes_not_applied")
    if not isinstance(axes, dict) or not isinstance(skipped, list):
        return None
    if not all(isinstance(item, str) and item.strip() for item in skipped):
        return None
    applied = {
        axis
        for axis in policy["axes"]
        if isinstance(axes.get(axis), dict)
        and axes[axis].get("status") in {"pass", "fail", "advisory"}
        and isinstance(axes[axis].get("evidence"), str)
        and axes[axis]["evidence"].strip()
    }
    skipped_axes = {
        axis
        for axis in policy["axes"]
        if any(
            item.lower().startswith(f"{axis}:") and item.split(":", 1)[1].strip()
            for item in skipped
        )
    }
    return applied, skipped_axes


def axis_evidence_valid(record: dict, policy: dict | None = None) -> bool:
    active = policy or load_policy()
    sets = _axis_sets(record, active)
    if sets is None:
        return False
    applied, skipped = sets
    required = set(active["axes"])
    return applied.isdisjoint(skipped) and applied | skipped == required


def shippable_axes_valid(record: dict, policy: dict | None = None) -> bool:
    active = policy or load_policy()
    if not axis_evidence_valid(record, active):
        return False
    axes = record["axes"]
    primaries = [axis for axis in active["primary_axes"] if isinstance(axes.get(axis), dict)]
    if len(primaries) != 1 or axes[primaries[0]].get("status") != "pass":
        return False
    return all(
        not isinstance(axes.get(axis), dict) or axes[axis].get("status") == "pass"
        for axis in active["optional_passing_axes"]
    )


def render_workflow(policy: dict | None = None) -> str:
    encoded = json.dumps(policy or load_policy(), sort_keys=True, separators=(",", ":"))
    return f"""{BEGIN}
const SHARPEN_POLICY = {encoded}

function objectiveGatePassed(candidate, efficacyMode) {{
  const mode = SHARPEN_POLICY.modes[efficacyMode ? 'efficacy' : 'conformance']
  return mode.gate_true.every((field) => candidate?.[field] === true) &&
    mode.gate_null.every((field) => candidate?.[field] === null) &&
    SHARPEN_POLICY.gate_not_false.every((field) => candidate?.[field] !== false)
}}
{END}"""


def synced_harness(harness: str, policy: dict | None = None) -> str:
    before, marker, rest = harness.partition(BEGIN)
    if not marker:
        raise ValueError("Sharpen harness lacks generated policy markers")
    _old, marker, after = rest.partition(END)
    if not marker:
        raise ValueError("Sharpen harness lacks generated policy end marker")
    return before + render_workflow(policy) + after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "sync", "gate"))
    parser.add_argument("--mode", choices=("efficacy", "conformance"))
    parser.add_argument("--gate-json")
    args = parser.parse_args()
    if args.command == "gate":
        if args.mode is None or args.gate_json is None:
            parser.error("gate requires --mode and --gate-json")
        passed = gate_passes(args.mode, json.loads(args.gate_json))
        print(json.dumps({"pass": passed, "mode": args.mode}))
        return 0 if passed else 1
    current = HARNESS_PATH.read_text(encoding="utf-8")
    expected = synced_harness(current)
    if args.command == "sync":
        HARNESS_PATH.write_text(expected, encoding="utf-8")
        return 0
    if current != expected:
        raise SystemExit("Sharpen Workflow policy is stale; run tools/sharpen_policy.py sync")
    print("sharpen-policy: generated Workflow evaluator is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
