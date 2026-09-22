from __future__ import annotations

import ast
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from telemetry_coverage import INVENTORY, causally_complete, coverage, main


@pytest.mark.parametrize(
    "scenario",
    json.loads(INVENTORY.read_text(encoding="utf-8"))["scenarios"],
    ids=lambda scenario: scenario["id"],
)
def test_each_declared_scenario_rejects_missing_required_evidence(scenario):
    """Schema oracle only; production execution is established by the referenced seam tests."""
    requirements = scenario["evidence"]
    events = []
    for requirement in requirements:
        args = dict.fromkeys(requirement.get("fields", []), "recorded")
        args.update({key: values[0] for key, values in requirement.get("values", {}).items()})
        if scenario.get("join"):
            args[scenario["join"]] = "operation-1"
        events.append({"ph": "X", "name": requirement["name"], "args": args})

    complete = coverage(events, [scenario])["scenarios"][0]
    missing = coverage(events[:-1], [scenario])["scenarios"][0]

    assert complete["status"] == ("observed" if requirements else "unobserved")
    assert missing["status"] != "observed"
    assert missing["missing_evidence_contract"] == scenario["diagnostic_contract"]
    assert missing["missing_evidence_contract"]["severity"] == "warning"
    assert missing["missing_evidence_contract"]["uncertainty"]
    assert missing["missing_evidence_contract"]["next_evidence"]
    assert complete["qualification"] == "not established by presence alone"


@pytest.mark.parametrize(
    ("trace", "status"),
    [(None, "missing"), ('{"traceEvents": []}', "readable"), ('{"traceEvents": [0]}', "invalid")],
)
def test_coverage_cli_cannot_promote_absent_or_invalid_capture(
    tmp_path, monkeypatch, capsys, trace, status
):
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("diagnostics/envelope.json", '{"schema": 1}')
        if trace is not None:
            archive.writestr("telemetry/trace.json", trace)
    monkeypatch.setattr("sys.argv", ["telemetry-coverage", str(source), "--require", "cue"])

    code = main()

    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["trace_evidence"]["status"] == status
    assert result["observed"] == 0


def test_missing_diagnostic_fails_the_declared_evidence_contract():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "telemetry_coverage", root / "tools/telemetry_coverage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scenarios = [
        {
            "id": "fault",
            "owner": "test",
            "critical": True,
            "tests": [],
            "evidence": [{"name": "request", "fields": ["outcome"]}],
        }
    ]
    healthy = [{"ph": "X", "name": "request", "args": {"outcome": "failed"}}]
    assert module.coverage(healthy, scenarios)["observed"] == 1
    assert module.coverage([], scenarios)["observed"] == 0
    assert module.coverage([{**healthy[0], "args": {}}], scenarios)["observed"] == 0
    scenarios[0]["evidence"][0]["values"] = {"outcome": ["failed"]}
    assert (
        module.coverage([{**healthy[0], "args": {"outcome": "succeeded"}}], scenarios)["observed"]
        == 0
    )


def test_successful_but_rejected_ack_cannot_complete_a_cue_color_chain():
    scenario = next(
        row
        for row in json.loads(INVENTORY.read_text(encoding="utf-8"))["scenarios"]
        if row["id"] == "cue-color"
    )
    events = [
        {"ph": "X", "name": "cue_reconcile", "args": {"cue": "opaque", "cue_revision": "1"}},
        {
            "ph": "X",
            "name": "surface_write",
            "args": {
                "cue_revision": "1",
                "effect_id": 2,
                "surface_revision": 3,
                "round_trip_ms": 1,
                "outcome": "succeeded",
                "accepted": False,
            },
        },
    ]

    result = coverage(events, [scenario])["scenarios"][0]

    assert result["status"] == "partial"
    assert result["causal_completeness"]["incomplete_operations"] == 1
    assert not causally_complete(result)


def test_inventory_is_nonempty_unique_and_points_to_existing_tests():
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "tests/fixtures/telemetry-scenarios.json").read_text())
    ids = [row["id"] for row in inventory["scenarios"]]
    assert ids and len(ids) == len(set(ids))
    assert all((root / test).is_file() for row in inventory["scenarios"] for test in row["tests"])
    cases = [case for row in inventory["scenarios"] for case in row.get("fault_cases", [])]
    assert {case["id"] for case in cases} == {
        "query-timeout",
        "query-disconnected",
        "query-unavailable",
    }
    assert len(cases) == 3
    assert all(
        {"injection", "expected_categories", "severity", "uncertainty", "next_evidence"}
        <= case.keys()
        for case in cases
    )
    bridges = inventory["bridge_cases"]
    assert {row["scenario"] for row in bridges} == set(ids) - {"character-masks"}
    assert len(bridges) == 14
    for bridge in bridges:
        file, *symbols = bridge["test"].split("::")
        body = ast.parse((root / file).read_text(encoding="utf-8")).body
        for symbol in symbols:
            node = next(node for node in body if getattr(node, "name", None) == symbol)
            body = getattr(node, "body", [])
        assert isinstance(node, ast.FunctionDef)
        assert bridge["endpoint"]


def test_one_completed_chain_does_not_hide_lost_or_unattributed_operations():
    scenario = {
        "id": "cue",
        "owner": "subtitle",
        "critical": True,
        "tests": [],
        "join": "revision",
        "evidence": [{"name": "request"}, {"name": "ack"}],
    }
    events = [
        {"ph": "X", "name": name, "args": {"revision": revision}}
        for name, revision in (
            ("request", 1),
            ("request", 2),
            ("ack", 1),
            ("ack", 3),
            ("ack", None),
            ("ack", True),
        )
    ]

    result = coverage(events, [scenario])["scenarios"][0]

    assert result["status"] == "observed"
    assert not causally_complete(result)
    assert result["causal_completeness"] == {
        "scope": "declared joined boundaries; first requirement is the start, not pixel proof",
        "started_operations": 2,
        "complete_operations": 1,
        "incomplete_operations": 1,
        "orphan_operations": 1,
        "missing_identity_events": 2,
        "invalid_schema_events": 0,
    }


def test_operation_identity_types_cannot_alias():
    scenario = {
        "id": "cue",
        "owner": "subtitle",
        "critical": True,
        "tests": [],
        "join": "revision",
        "evidence": [{"name": "request"}, {"name": "ack"}],
    }
    events = [
        {"ph": "X", "name": "request", "args": {"revision": 1}},
        {"ph": "X", "name": "ack", "args": {"revision": "1"}},
    ]

    result = coverage(events, [scenario])["scenarios"][0]

    assert result["joined_operations"] == 0
    assert not causally_complete(result)


@pytest.mark.parametrize("loss", ["none", "malformed", "dropped"])
def test_complete_cli_cannot_certify_only_the_retained_tail(tmp_path, monkeypatch, capsys, loss):
    events = [
        {
            "ph": "X",
            "name": name,
            "ts": 1,
            "dur": 1,
            "args": {"view_id": "v1", "job_id": "j1", "outcome": "succeeded", "latency_ms": 1},
        }
        for name in ("render_ahead_request", "scroll_request")
    ]
    if loss == "malformed":
        events.append(
            {
                "ph": "X",
                "name": "render_ahead_request",
                "ts": 2,
                "args": {"view_id": "v2", "job_id": "j2"},
            }
        )
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("telemetry/trace.json", json.dumps({"traceEvents": events}))
        archive.writestr(
            "telemetry/health.json", json.dumps({"queue_dropped": int(loss == "dropped")})
        )
    monkeypatch.setattr(
        "sys.argv", ["telemetry-coverage", str(source), "--require-complete", "tooltip-worker"]
    )

    result = main()

    capsys.readouterr()
    assert result == int(loss != "none")


@pytest.mark.parametrize("drop_field", [False, True])
def test_complete_chain_gate_detects_schema_loss_in_another_operation(drop_field):
    scenario = {
        "id": "cue",
        "owner": "subtitle",
        "critical": True,
        "tests": [],
        "join": "revision",
        "evidence": [{"name": "request"}, {"name": "ack", "fields": ["outcome"]}],
    }
    events = [
        {"ph": "X", "name": name, "args": {"revision": revision, "outcome": "ok"}}
        for revision in (1, 2)
        for name in ("request", "ack")
    ]
    if drop_field:
        events[-1]["args"].pop("outcome")

    result = coverage(events, [scenario])["scenarios"][0]

    assert result["status"] == "observed"
    assert causally_complete(result) is not drop_field
    assert result["causal_completeness"]["invalid_schema_events"] == int(drop_field)


@pytest.mark.parametrize("supply_missing", [False, True])
def test_character_gate_rejects_absent_or_unreadable_corpora(
    tmp_path, monkeypatch, capsys, supply_missing
):
    source = tmp_path / "trace.json"
    source.write_text('{"traceEvents": []}', encoding="utf-8")
    argv = ["telemetry-coverage", str(source), "--require-character-qualification"]
    if supply_missing:
        argv += ["--character-corpus", str(tmp_path / "missing")]
    monkeypatch.setattr("sys.argv", argv)

    assert main() == 1

    result = json.loads(capsys.readouterr().out)
    assert result["character_corpora"] == (
        [
            {
                "status": "invalid",
                "reason": "missing, malformed, oversized or mismatched character artifacts",
            }
        ]
        if supply_missing
        else []
    )
