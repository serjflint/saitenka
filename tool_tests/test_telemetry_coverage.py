from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from telemetry_coverage import main


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


def test_inventory_is_nonempty_unique_and_points_to_existing_tests():
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "tests/fixtures/telemetry-scenarios.json").read_text())
    ids = [row["id"] for row in inventory["scenarios"]]
    assert ids and len(ids) == len(set(ids))
    assert all((root / test).is_file() for row in inventory["scenarios"] for test in row["tests"])
