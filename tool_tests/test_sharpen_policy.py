"""Shared-policy tests for the Sharpen Python and generated Workflow adapters."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import sharpen_policy as sp


def _gate(**changes: object) -> dict:
    gate = {
        "pass": True,
        "anticheat_clean": True,
        "efficacy_pass": None,
        "conformance_pass": True,
        "preservation_pass": None,
        "restoration_verified": True,
    }
    return {**gate, **changes}


def _record(primary: str) -> dict:
    other = "conformance" if primary == "efficacy" else "efficacy"
    return {
        "axes": {primary: {"status": "pass", "evidence": "measured"}},
        "axes_not_applied": [
            f"{other}: inactive primary axis",
            "preservation: no assertion changed",
            "brittleness: probe unavailable",
            "redundancy: advisory not run",
        ],
    }


def test_gate_modes_accept_only_their_active_primary_axis():
    assert sp.gate_passes("conformance", _gate())
    assert not sp.gate_passes("conformance", _gate(efficacy_pass=True))
    assert sp.gate_passes("efficacy", _gate(efficacy_pass=True, conformance_pass=None))
    assert not sp.gate_passes("efficacy", _gate(efficacy_pass=False, conformance_pass=None))


def test_shippable_axes_require_an_exact_partition_and_one_primary():
    assert sp.shippable_axes_valid(_record("efficacy"))
    assert sp.shippable_axes_valid(_record("conformance"))
    contradictory = _record("efficacy")
    contradictory["axes_not_applied"].append("efficacy: contradictory")
    assert not sp.shippable_axes_valid(contradictory)
    both = _record("efficacy")
    both["axes"]["conformance"] = {"status": "pass", "evidence": "also applied"}
    both["axes_not_applied"] = [
        item for item in both["axes_not_applied"] if not item.startswith("conformance:")
    ]
    assert not sp.shippable_axes_valid(both)


def test_generated_workflow_policy_is_current():
    harness = sp.HARNESS_PATH.read_text(encoding="utf-8")
    assert sp.synced_harness(harness) == harness
    generated = harness.split(sp.BEGIN, 1)[1].split(sp.END, 1)[0]
    assert harness.count("function objectiveGatePassed") == 1
    assert "function objectiveGatePassed" in generated


def test_ledger_delegates_policy_instead_of_reimplementing_it():
    ledger = (sp.ROOT / "tools/sharpen_ledger.py").read_text(encoding="utf-8")
    assert "policy.axis_evidence_valid(record)" in ledger
    assert "return policy.shippable_axes_valid(record)" in ledger
    assert 'REQUIRED_AXES = set(policy.load_policy()["axes"])' in ledger
