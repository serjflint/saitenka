import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from test_mpv_gateway import FakeIPC
from test_render_evidence import _setup as _runtime_setup

from saitenka.app import report, telemetry
from saitenka.app.session.mpv_gateway import MpvGateway
from saitenka.runtime import SessionMailbox

pytestmark = pytest.mark.usefixtures("enabled_telemetry")


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


def fault_cases(scenario):
    inventory = json.loads(
        (Path(__file__).parent / "fixtures/telemetry-scenarios.json").read_text(encoding="utf-8")
    )
    row = next(row for row in inventory["scenarios"] if row["id"] == scenario)
    return [{**case, "required_evidence": row["fault_evidence"]} for case in row["fault_cases"]]


def assert_fault_contract(result, case):
    assert _load_findings().evaluate_fault(result, case)["passed"]
    assert {row["category"] for row in result["findings"]} == set(case["expected_categories"])
    assert all(row["severity"] == case["severity"] for row in result["findings"])
    assert all(row["next_evidence"] == case["next_evidence"] for row in result["findings"])
    assert case["uncertainty"] == "displayed-fidelity-unknown"
    assert result["fidelity"]["status"] == "unknown"


@pytest.mark.parametrize(
    "case",
    fault_cases("mpv-failure"),
    ids=lambda case: case["id"],
)
def test_missing_evidence_cannot_satisfy_a_fault_contract(case):
    assert not _load_findings().evaluate_fault(diagnose({}), case)["passed"]


def _setup(monkeypatch, tmp_path):
    _runtime_setup(monkeypatch, tmp_path)


@pytest.mark.timeout(5)
@pytest.mark.parametrize("case", fault_cases("mpv-failure"), ids=lambda case: case["id"])
def test_gateway_fault_is_diagnosable_from_default_zip_without_trace(case, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    class Replies(FakeIPC):
        def command(self, *args, **kwargs):
            super().command(*args, **kwargs)
            return {"error": case["injection"], "data": "PRIVATE-CUE"}

    gateway = MpvGateway(Replies(), SessionMailbox())
    try:
        gateway.register_observers(("osd-dimensions",))
        telemetry.save_operation_summary()
        bundle = report.build_report_bundle(tmp_path, timestamp="fault")
    finally:
        gateway.close()

    result = diagnose(read_envelope(bundle))

    assert result["status"] == "fault-observed"
    assert_fault_contract(result, case)
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


@pytest.mark.timeout(5)
def test_failed_raster_upload_is_diagnosed_from_metadata_without_pixel_claim(monkeypatch, tmp_path):
    from test_native_subtitles import (
        Coloring,
        KnownWords,
        Scorer,
        attachment_supplying,
        reader,
        settle_jobs,
    )

    _setup(monkeypatch, tmp_path)
    session, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    try:
        geometry = session.graph.subtitle_presentation.native
        assert geometry is not None
        geometry.set_fonts(attachment_supplying(ipc, "arial"))
        ipc.overlay_add_error = "PRIVATE"
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)
        telemetry.save_operation_summary()
        bundle = report.build_report_bundle(tmp_path, timestamp="raster-failure")
    finally:
        session.close()

    result = diagnose(read_envelope(bundle))

    assert any(
        row["category"] == "operation-failed"
        and row["evidence"]["operation"] == "subtitle_device_upload"
        for row in result["findings"]
    )
    assert result["fidelity"]["status"] == "unknown"
    with zipfile.ZipFile(bundle) as archive:
        assert not any("trace" in name for name in archive.namelist())
        assert b"PRIVATE" not in b"".join(archive.read(name) for name in archive.namelist())


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


@pytest.mark.parametrize(
    "fault",
    [
        "truncated",
        "partial",
        "lost_events",
        "queue_dropped",
        "write_failures",
        "history_losses",
        "sample_failures",
        "healthy",
    ],
)
def test_trace_capture_fault_survives_collection_zip_and_diagnosis(monkeypatch, tmp_path, fault):
    config = _runtime_setup(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir(exist_ok=True)
    config.write_text(f'[telemetry]\nexport_dir = "{directory.as_posix()}"\n', encoding="utf-8")
    events = [{"name": "cue_reconcile", "ph": "X", "ts": 0, "dur": 1}]
    if fault == "partial":
        events.append(None)
    document = json.dumps({"otherData": {"session": "runtime-test"}, "traceEvents": events})
    (directory / "trace-1.json").write_text(
        document[:-1] if fault == "truncated" else document, encoding="utf-8"
    )
    (directory / "trace-1.health.json").write_text(
        json.dumps({"session": "runtime-test", fault: 3}),
        encoding="utf-8",
    )

    bundle = report.build_report_bundle(tmp_path / "reports", diagnostic_detail=True)
    result = _load_findings().diagnose_report(bundle)

    capture = [row for row in result["findings"] if row["category"] == "trace-incomplete"]
    assert len(capture) == (0 if fault == "healthy" else 1)
    if capture:
        assert capture[0]["severity"] == "warning"
        assert "product failure not established" in capture[0]["cause"]
        assert capture[0]["next_evidence"] == (
            "compatible complete trace or same-session operation summary"
        )
        assert capture[0]["evidence"]["status"] == {
            "truncated": "missing",
            "partial": "partial",
        }.get(fault, "readable")
    assert result["fidelity"]["status"] == "unknown"


@pytest.mark.parametrize("bad", [True, -1, "PRIVATE", [], {}])
def test_malformed_capture_accounting_is_not_a_product_failure(tmp_path, bad):
    source = tmp_path / "report.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("telemetry/collection.json", json.dumps({"candidates": [{"status": bad}]}))
        archive.writestr("telemetry/health.json", json.dumps({"lost_events": bad}))

    result = _load_findings().diagnose_report(source)

    assert result["findings"] == []
    assert result["status"] == "unidentified"
    assert result["fidelity"]["status"] == "unknown"
