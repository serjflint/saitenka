"""Integration failure evidence survives export and tracing-disabled collection."""

import json

import pytest
from ankiconnect_client import AnkiConnectClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from test_diagnostic_findings import _load_findings
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
    archive = report.build_report_bundle(
        tmp_path / "reports", timestamp="integration", diagnostic_detail=True
    )
    diagnosis = _load_findings().diagnose_report(archive)
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


def test_enabling_tracing_externally_still_resolves_the_module(monkeypatch):
    """`_trace_available` answers "may we trace"; `_trace_module` answers "through what".

    Setting the first without the second used to turn tracing OFF — the availability check was
    what populated the module, so skipping it left `_trace_module` at `None` and every span became
    a silent no-op. That is the state every test above sets up, which made their tracing
    assertions pass only when some earlier test in the same process had resolved the module first:
    run the file alone and the two `tracing=True` cases failed.
    """
    monkeypatch.setattr(otel_metrics, "_trace_available", True)
    monkeypatch.setattr(otel_metrics, "_trace_module", None)

    assert otel_metrics._resolve_trace_module() is not None


def test_a_confirmed_absent_extra_is_still_never_re_imported(monkeypatch):
    """The negative half: `False` means "checked, not installed", and it must stay sticky.

    Keying the import on `_trace_module is None` would otherwise re-attempt a failing import on
    every call from a hot path like `traced()`, which is the cost the memo exists to avoid.
    """
    monkeypatch.setattr(otel_metrics, "_trace_available", False)
    monkeypatch.setattr(otel_metrics, "_trace_module", None)
    monkeypatch.delitem(__import__("sys").modules, "opentelemetry.trace", raising=False)

    assert otel_metrics._resolve_trace_module() is None
