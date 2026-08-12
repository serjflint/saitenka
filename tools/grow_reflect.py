"""Read/write library for the reflection ledger (`.reflection.grow.jsonl`, repo top level).

Every Grow run ends with a self-reflection step (SPEC → *Self-reflection*): an isolated agent introspects
the run's trace, reflects on what was inefficient / wrong / suboptimal about the LOOP ITSELF, and files
concrete improvement proposals here. It is **advisory** — it never edits the loop's tools; a human triages.
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

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path  # annotation-only — grow_reflect never constructs a Path

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
