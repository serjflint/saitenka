"""Integration evidence is exported only when tracing is enabled."""

import json

import pytest
from ankiconnect_client import AnkiConnectClient
from test_diagnostic_findings import _load_findings
from test_report import _hermetic

from saitenka.app import anki, report, telemetry
from saitenka.app.trace_report import load_startup_trace
from saitenka.session import session_id


@pytest.mark.parametrize("tracing", [True, False])
@pytest.mark.parametrize("failure", ["unavailable", "protocol"])
def test_anki_failure_remains_diagnosable_without_payloads(monkeypatch, tmp_path, tracing, failure):
    config = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    config.write_text(
        f'[telemetry]\nenabled = {str(tracing).lower()}\nexport_dir = "{directory.as_posix()}"\n'
    )
    (tmp_path / "cache/overlay.log").write_text(json.dumps({"session": session_id()}) + "\n")
    from telemetry_helpers import enable_telemetry

    telemetry.shutdown()
    if tracing:
        enable_telemetry(monkeypatch, tmp_path)
        config.write_text(
            f'[telemetry]\nenabled = true\nexport_dir = "{(tmp_path / "trace").as_posix()}"\n'
        )
    monkeypatch.setattr("ankiconnect_client.client.time.sleep", lambda _seconds: None)

    class Transport:
        def send(self, _payload, *, timeout):
            assert timeout > 0
            if failure == "unavailable":
                raise OSError("private transport message")
            return {"error": "private response"}

    client = AnkiConnectClient(transport=Transport())
    monkeypatch.setattr(anki, "AnkiConnectClient", lambda *_args: client)
    with pytest.raises((anki.AnkiError, OSError)):
        anki.Anki().find_notes("private note query")
    telemetry.save_operation_summary()
    telemetry.shutdown()
    archive = report.build_report_bundle(
        tmp_path / "reports", timestamp="integration", diagnostic_detail=True
    )
    diagnosis = _load_findings().diagnose_report(archive)
    if not tracing:
        import zipfile

        with zipfile.ZipFile(archive) as bundle:
            envelope = json.loads(bundle.read("diagnostics/envelope.json"))
            assert envelope["operation_health"] == {"status": "not-collected"}
            assert "diagnostics/session.json" not in bundle.namelist()
        return
    assert any(
        finding["category"]
        == ("operation-unavailable" if failure == "unavailable" else "operation-other")
        and finding["evidence"]["operation"] == "anki_request"
        and finding["severity"] == "warning"
        for finding in diagnosis["findings"]
    )
    assert diagnosis["fidelity"]["status"] == "unknown"
    import zipfile

    with zipfile.ZipFile(archive) as bundle:
        envelope = json.loads(bundle.read("diagnostics/envelope.json"))
        assert envelope["operation_health"]["by_operation"]["anki_request"] == {
            "unavailable" if failure == "unavailable" else "other": 1
        }
        summary = json.loads(bundle.read("diagnostics/session.json"))
        assert summary["pending"] == {}
        assert summary["outcomes"] == [
            {
                "operation": "anki_request",
                "outcome": "unavailable"
                if failure == "unavailable"
                else "AnkiConnectProtocolError",
                "count": 1,
            }
        ]
        assert "private" not in bundle.read("diagnostics/session.json").decode()
    if tracing:
        events = load_startup_trace(archive)
        request = next(event for event in events if event.get("name") == "anki_request")
        attempts = [event for event in events if event.get("name") == "anki_attempt"]
        assert len(attempts) == (2 if failure == "unavailable" else 1)
        assert all(event["args"]["parent_id"] == request["args"]["span_id"] for event in attempts)
        assert all(event["args"]["status"] == "error" for event in attempts)
        assert "private" not in json.dumps(events)
