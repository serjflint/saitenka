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


def _unique_strings(value: object) -> bool:
    return bool(
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item for item in value)
        and len(value) == len(set(value))
    )


def validate_policy(policy: dict) -> None:
    modes = policy.get("modes")
    if (
        policy.get("version") != 1
        or not isinstance(modes, dict)
        or set(modes) != {"efficacy", "conformance"}
    ):
        raise ValueError("unsupported Sharpen policy")
    for field in ("axes", "primary_axes", "optional_passing_axes", "gate_not_false"):
        if not _unique_strings(policy.get(field)):
            raise ValueError(f"Sharpen policy requires unique non-empty {field}")
    axes = set(policy["axes"])
    primaries = set(policy["primary_axes"])
    optional = set(policy["optional_passing_axes"])
    if set(modes) != primaries or not primaries <= axes:
        raise ValueError("Sharpen policy axes do not match its modes")
    if not optional <= axes or optional & primaries:
        raise ValueError("Sharpen policy optional axes are invalid")
    common_gate_fields: set[str] | None = None
    for name, mode in modes.items():
        if not isinstance(mode, dict):
            raise TypeError(f"Sharpen policy mode {name} must be an object")
        active_field = f"{name}_pass"
        inactive_fields = {f"{other}_pass" for other in primaries - {name}}
        if (
            mode.get("primary_axis") != name
            or not _unique_strings(mode.get("gate_true"))
            or not _unique_strings(mode.get("gate_null"))
        ):
            raise ValueError(f"Sharpen policy mode {name} is incoherent")
        true_fields = set(mode["gate_true"])
        null_fields = set(mode["gate_null"])
        not_false_fields = set(policy["gate_not_false"])
        common = true_fields - {active_field}
        if (
            active_field not in true_fields
            or null_fields != inactive_fields
            or not common
            or true_fields & null_fields
            or (true_fields | null_fields) & not_false_fields
        ):
            raise ValueError(f"Sharpen policy mode {name} has unsafe gate requirements")
        if common_gate_fields is None:
            common_gate_fields = common
        elif common != common_gate_fields:
            raise ValueError("Sharpen policy modes require different common gates")


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    validate_policy(policy)
    return policy


def gate_passes(mode: str, gate: dict, policy: dict | None = None) -> bool:
    current = policy or load_policy()
    validate_policy(current)
    active = current["modes"].get(mode)
    if not isinstance(active, dict):
        return False
    required = [*active["gate_true"], *active["gate_null"], *current["gate_not_false"]]
    return bool(
        all(field in gate for field in required)
        and all(gate.get(field) is True for field in active["gate_true"])
        and all(gate.get(field) is None for field in active["gate_null"])
        and all(
            gate.get(field) is True or gate.get(field) is None
            for field in current["gate_not_false"]
        )
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
    validate_policy(active)
    sets = _axis_sets(record, active)
    if sets is None:
        return False
    applied, skipped = sets
    required = set(active["axes"])
    return applied.isdisjoint(skipped) and applied | skipped == required


def shippable_axes_valid(record: dict, policy: dict | None = None) -> bool:
    active = policy or load_policy()
    validate_policy(active)
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
    current = policy or load_policy()
    validate_policy(current)
    encoded = json.dumps(current, sort_keys=True, separators=(",", ":"))
    return f"""{BEGIN}
const SHARPEN_POLICY = {encoded}

const objectiveGatePassed = (candidate, efficacyMode) => {{
  const mode = SHARPEN_POLICY.modes[efficacyMode ? 'efficacy' : 'conformance']
  const required = [...mode.gate_true, ...mode.gate_null, ...SHARPEN_POLICY.gate_not_false]
  return required.every((field) => Object.hasOwn(candidate ?? {{}}, field)) &&
    mode.gate_true.every((field) => candidate[field] === true) &&
    mode.gate_null.every((field) => candidate?.[field] === null) &&
    SHARPEN_POLICY.gate_not_false.every((field) => candidate[field] === true || candidate[field] === null)
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
    parser.add_argument("--gate-file", type=Path)
    args = parser.parse_args()
    if args.command == "gate":
        if args.mode is None or args.gate_file is None:
            parser.error("gate requires --mode and --gate-file")
        passed = gate_passes(args.mode, json.loads(args.gate_file.read_text(encoding="utf-8")))
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
