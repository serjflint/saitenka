import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from test_mpv_gateway import FakeIPC
from test_render_evidence import _setup as _runtime_setup

from saitenka import operation_summary
from saitenka.app import report, telemetry
from saitenka.app.session.mpv_gateway import MpvGateway
from saitenka.runtime import SessionMailbox


def _load_findings():
    spec = importlib.util.spec_from_file_location(
        "diagnostic_findings", Path(__file__).parents[1] / "tools/diagnostic_findings.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = _load_findings().diagnose
read_envelope = _load_findings().read_envelope


def _setup(monkeypatch, tmp_path):
    _runtime_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(operation_summary, "operations", operation_summary.OperationSummary())


@pytest.mark.timeout(5)
@pytest.mark.parametrize("outcome", ["timeout", "disconnected", "property unavailable"])
def test_gateway_fault_is_diagnosable_from_default_zip_without_trace(
    outcome, monkeypatch, tmp_path
):
    _setup(monkeypatch, tmp_path)

    class Replies(FakeIPC):
        def command(self, *args, **kwargs):
            super().command(*args, **kwargs)
            return {"error": outcome, "data": "PRIVATE-CUE"}

    gateway = MpvGateway(Replies(), SessionMailbox())
    try:
        gateway.register_observers(("osd-dimensions",))
        telemetry.save_operation_summary()
        bundle = report.build_report_bundle(tmp_path, timestamp="fault")
    finally:
        gateway.close()

    result = diagnose(read_envelope(bundle))

    assert result["status"] == "fault-observed"
    assert {row["category"] for row in result["findings"]} == {
        "player-query-" + outcome.replace(" ", "-"),
    }
    assert {row["evidence"]["verb"] for row in result["findings"]} == {
        "observe_property",
        "get_property",
    }
    assert result["fidelity"]["status"] == "unknown"
    with zipfile.ZipFile(bundle) as archive:
        assert not any("trace" in name for name in archive.namelist())
        assert b"PRIVATE-CUE" not in b"".join(archive.read(name) for name in archive.namelist())


def test_mailbox_saturation_is_diagnosed_without_claiming_render_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, SessionMailbox(normal_capacity=1))
    try:
        ipc.publish({"event": "file-loaded"})
        ipc.publish({"event": "property-change", "name": "osd-dimensions", "data": {}})
        telemetry.save_operation_summary()
        bundle = report.build_report_bundle(tmp_path, timestamp="full")
    finally:
        gateway.close()

    result = diagnose(read_envelope(bundle))

    assert [row["category"] for row in result["findings"]] == ["player-ingress-mailbox-full"]
    assert result["causal_completeness"]["admitted_retained"] == 0
    assert result["fidelity"]["status"] == "unknown"


@pytest.mark.parametrize("payload", [None, {}, {"schema": 99}, {"schema": True}])
def test_missing_or_incompatible_metadata_is_not_a_successful_diagnosis(tmp_path, payload):
    bundle = tmp_path / "report.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        if payload is not None:
            archive.writestr("diagnostics/envelope.json", json.dumps(payload))

    result = diagnose(read_envelope(bundle))

    assert result["status"] == "unidentified"
    assert result["findings"] == []
    assert result["causal_completeness"]["status"] == "unknown"


@pytest.mark.parametrize("outcome", ["unavailable", "failed", "pending"])
def test_operation_findings_identify_boundary_without_inventing_a_root_cause(outcome):
    result = diagnose({"operation_health": {"by_operation": {"anki_request": {outcome: 2}}}})

    assert result["status"] == "fault-observed"
    assert result["findings"][0]["evidence"] == {"operation": "anki_request", "count": 2}
    assert result["findings"][0]["category"] == "operation-" + outcome
    assert result["fidelity"]["status"] == "unknown"
    assert "root cause unknown" in result["findings"][0]["cause"]


@pytest.mark.parametrize(
    "counts", [{"failed": True}, {"failed": -1}, {"failed": "PRIVATE"}, {"succeeded": 1}]
)
def test_operation_findings_do_not_promote_invalid_or_healthy_counts(counts):
    result = diagnose(
        {"operation_health": {"by_operation": {"anki_request": counts, "PRIVATE": {"failed": 1}}}}
    )

    assert result["findings"] == []
    assert result["status"] == "unidentified"
