"""Shared-policy tests for the Sharpen Python and generated Workflow adapters."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

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
    missing_preservation = {
        key: value for key, value in _gate().items() if key != "preservation_pass"
    }
    assert not sp.gate_passes("conformance", missing_preservation)
    assert not sp.gate_passes("conformance", _gate(preservation_pass=False))
    assert not sp.gate_passes("conformance", _gate(preservation_pass=1))


def test_policy_schema_rejects_vacuous_or_incoherent_rules():
    valid = sp.load_policy()
    mutations = []
    for path, value in (
        (("axes",), []),
        (("primary_axes",), ["efficacy"]),
        (("optional_passing_axes",), []),
        (("gate_not_false",), []),
        (("modes", "efficacy", "gate_true"), []),
        (("modes", "efficacy", "gate_null"), []),
        (("modes", "efficacy", "primary_axis"), "conformance"),
        (("modes", "efficacy"), []),
    ):
        changed = copy.deepcopy(valid)
        owner = changed
        for key in path[:-1]:
            owner = owner[key]
        owner[path[-1]] = value
        mutations.append(changed)
    for malformed in mutations:
        with pytest.raises((TypeError, ValueError)):
            sp.validate_policy(malformed)


def test_gate_cli_reads_host_validated_json_file(tmp_path, monkeypatch, capsys):
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(_gate()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sharpen_policy.py",
            "gate",
            "--mode",
            "conformance",
            "--gate-file",
            str(gate_path),
        ],
    )
    assert sp.main() == 0
    assert capsys.readouterr().out == '{"pass": true, "mode": "conformance"}\n'


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
    assert harness.count("const objectiveGatePassed =") == 1
    assert "const objectiveGatePassed =" in generated
    assert harness.count("objectiveGatePassed(") == 2


def test_ledger_delegates_policy_instead_of_reimplementing_it():
    ledger = (sp.ROOT / "tools/sharpen_ledger.py").read_text(encoding="utf-8")
    assert "policy.axis_evidence_valid(record)" in ledger
    assert "return policy.shippable_axes_valid(record)" in ledger
    assert 'REQUIRED_AXES = set(policy.load_policy()["axes"])' in ledger
