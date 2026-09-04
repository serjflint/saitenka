"""Read/write library for the reflection ledger (`.reflection.grow.jsonl`, repo top level).

Every completed Grow outcome passes a self-reflection step before its Grow receipt (SPEC →
*Self-reflection*): an isolated agent introspects
the run's trace, reflects on what was inefficient / wrong / suboptimal about the LOOP ITSELF, and files
concrete improvement proposals here. It is **advisory** — it never edits the loop's tools; a human triages.
The ledger also carries one deterministic `run-reflection` receipt per trace, including clean runs with no
findings, so the Grow ledger can verify that reflection actually persisted.
Both live runs so far found real loop-design bugs (run 1 → 8 flaws; run 2 → the arm-1/arm-3 composition
bug), so this turns those accidental discoveries into a standing mechanism.

A finding's identity is SEMANTIC: `finding_id = hash(category, subject)`, so the same weakness observed
across runs accumulates instead of duplicating. Recurrence is counted **at the current `loop_version`**
(the manifest field, mirroring Sharpen's `toolset_version`): when a human lands a loop-improvement they bump
`loop_version`, which resets the accumulation — a finding that persists past the fix re-accumulates and
re-escalates, one that was truly fixed goes quiet. A finding seen ≥ the escalation threshold is surfaced to
the human (or drafted as an issue) rather than sitting in the ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = (
    "gate-composition",  # arms combined wrongly (e.g. AND where OR was right)
    "arm-limitation",  # an arm's mechanism misses a class it should catch
    "triage-signal",  # ranking driven by a weak/inverted proxy
    "discovery",  # scenario-map picks wrong / misses orphans
    "cli-ergonomics",  # a tool CLI can't express what the loop needs
    "cost-latency",  # a stage is disproportionately slow/expensive
    "review-fidelity",  # isolation / independence / sycophancy risk
    "false-bounce",  # the gate rejected a legitimate grow
    "false-pass",  # the gate passed a weak/vacuous grow
    "other",
)
SEVERITIES = ("low", "medium", "high")


def finding_id(category: str, subject: str) -> str:
    """Semantic, position-free — the same weakness gets the same id across runs so it accumulates."""
    return hashlib.sha256(f"{category}\x00{subject}".encode()).hexdigest()[:16]


@dataclass
class ReflectionLedger:
    path: Path
    lines: list[dict]

    @classmethod
    def load(cls, path: Path) -> ReflectionLedger:
        recs = [
            json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        return cls(path, recs)

    @property
    def manifest(self) -> dict:
        return next((r for r in self.lines if r.get("type") == "manifest"), {})

    @property
    def loop_version(self) -> int:
        return int(self.manifest.get("loop_version", 1))

    def _findings(self) -> list[dict]:
        return [r for r in self.lines if "finding_id" in r]

    def run_receipt(self, reflection_id: str) -> dict | None:
        for record in reversed(self.lines):
            if (
                record.get("type") == "run-reflection"
                and record.get("reflection_id") == reflection_id
            ):
                return record
        return None

    def run_receipt_sequence_valid(self) -> bool:
        receipts = [record for record in self.lines if record.get("type") == "run-reflection"]
        return [record.get("sequence") for record in receipts] == list(range(1, len(receipts) + 1))

    def recurrence(self, fid: str) -> int:
        """How many times this finding has been filed AT THE CURRENT loop_version (a version bump — a
        landed loop-improvement — resets the count, so only findings that outlive the fix keep climbing)."""
        v = self.loop_version
        return sum(
            1 for r in self._findings() if r["finding_id"] == fid and r.get("loop_version") == v
        )

    def escalated(self, threshold: int = 2) -> list[dict]:
        """The latest record of each finding whose recurrence at the current loop_version ≥ threshold —
        surfaced to the human (or drafted as an issue), not left to accrete silently."""
        latest: dict[str, dict] = {}
        for r in self._findings():
            if r.get("loop_version") == self.loop_version:
                latest[r["finding_id"]] = r  # records are chronological → last wins
        return [r for fid, r in latest.items() if self.recurrence(fid) >= threshold]

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.lines.append(record)


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def reflection_id(record: dict) -> str:
    core = {
        key: record.get(key)
        for key in (
            "sequence",
            "trace_sha",
            "introspection",
            "finding_ids",
            "escalations",
            "loop_version",
        )
    }
    return _canonical_sha(core)[:16]


def prepare_run(record: dict, ledger: ReflectionLedger) -> tuple[list[dict], dict]:
    trace = record.get("trace")
    if not isinstance(trace, dict):
        raise TypeError("reflection requires a trace object")
    introspection = record.get("introspection")
    if not isinstance(introspection, str) or not introspection.strip():
        raise ValueError("reflection requires non-empty introspection")
    findings = record.get("findings")
    escalations = record.get("escalations")
    if not isinstance(findings, list) or not isinstance(escalations, list):
        raise TypeError("reflection findings and escalations must be lists")
    prepared_findings: list[dict] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise TypeError("reflection finding must be an object")
        category = finding.get("category")
        subject = finding.get("subject")
        if category not in CATEGORIES or not isinstance(subject, str) or not subject.strip():
            raise ValueError("reflection finding has invalid category or subject")
        if finding.get("severity") not in SEVERITIES:
            raise ValueError("reflection finding has invalid severity")
        for key in ("evidence", "proposal"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                raise ValueError(f"reflection finding requires {key}")
        if not isinstance(finding.get("self_referential"), bool):
            raise TypeError("reflection finding requires self_referential")
        prepared_findings.append(
            {
                **finding,
                "finding_id": finding_id(category, subject),
                "loop_version": ledger.loop_version,
            }
        )
    if not all(isinstance(item, str) and item for item in escalations):
        raise ValueError("reflection escalations must be non-empty strings")
    trace_sha = _canonical_sha(trace)
    sequence = sum(line.get("type") == "run-reflection" for line in ledger.lines) + 1
    receipt = {
        "type": "run-reflection",
        "sequence": sequence,
        "trace_sha": trace_sha,
        "trace": trace,
        "introspection": introspection,
        "finding_ids": [finding["finding_id"] for finding in prepared_findings],
        "escalations": escalations,
        "loop_version": ledger.loop_version,
    }
    receipt["reflection_id"] = reflection_id(receipt)
    return prepared_findings, receipt


def append_run(record: dict, ledger: ReflectionLedger) -> dict:
    findings, receipt = prepare_run(record, ledger)
    for finding in findings:
        ledger.append(finding)
    ledger.append(receipt)
    return {
        "reflection_id": receipt["reflection_id"],
        "trace_sha": receipt["trace_sha"],
        "introspection": receipt["introspection"],
        "findings": record["findings"],
        "appended": True,
        "escalations": receipt["escalations"],
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path(".reflection.grow.jsonl"))
    parser.add_argument("command", choices=("append-run",))
    records = parser.add_mutually_exclusive_group(required=True)
    records.add_argument("--record-json")
    records.add_argument("--record-file", type=Path)
    args = parser.parse_args()
    if not args.ledger.exists():
        args.ledger.write_text('{"type":"manifest","loop_version":1}\n', encoding="utf-8")
    ledger = ReflectionLedger.load(args.ledger)
    raw = args.record_file.read_text(encoding="utf-8") if args.record_file else args.record_json
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise TypeError("record must be a JSON object")
    print(json.dumps(append_run(record, ledger), ensure_ascii=False))
    return 0


def main() -> int:
    try:
        return _main()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"grow-reflect: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
