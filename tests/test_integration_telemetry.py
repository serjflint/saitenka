"""Integration failure evidence survives export and tracing-disabled collection."""

import json

import pytest
from ankiconnect_client import AnkiConnectClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from test_report import _hermetic

from saitenka import operation_summary, otel_metrics
from saitenka.app import anki, report, telemetry
from saitenka.app.otel_export import CTFSpanProcessor
from saitenka.app.trace_report import load_startup_trace
from saitenka.session import session_id


@pytest.mark.parametrize("tracing", [True, False])
@pytest.mark.parametrize("failure", ["unavailable", "protocol"])
def test_anki_failure_remains_diagnosable_without_payloads(monkeypatch, tmp_path, tracing, failure):
    config = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    config.write_text(
        f'[telemetry]\nenabled = {str(tracing).lower()}\nexport_dir = "{directory}"\n'
    )
    (tmp_path / "cache/overlay.log").write_text(json.dumps({"session": session_id()}) + "\n")
    monkeypatch.setattr(operation_summary, "operations", operation_summary.OperationSummary())
    monkeypatch.setattr(otel_metrics, "_trace_available", tracing)
    monkeypatch.setattr("ankiconnect_client.client.time.sleep", lambda _seconds: None)
    gate = telemetry.ActiveGate()
    gate.set(value=tracing)
    provider = TracerProvider()
    provider.add_span_processor(
        CTFSpanProcessor(directory / "trace-1.json", gate, start_thread=False)
    )
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

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
    provider.shutdown()
    archive = report.build_report_bundle(tmp_path / "reports", timestamp="integration")
    import zipfile

    with zipfile.ZipFile(archive) as bundle:
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
