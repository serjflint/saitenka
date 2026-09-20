"""Explicit opt-in for tests that inspect the real telemetry writer's artifacts."""

from opentelemetry import trace

from saitenka import otel_metrics
from saitenka.app import telemetry
from saitenka.app.config import TelemetryOptions


def enable_span_gate(monkeypatch):
    gate = telemetry.ActiveGate()
    gate.set(value=True)
    monkeypatch.setattr(telemetry, "span_gate", gate)
    monkeypatch.setattr(otel_metrics, "span_gate", gate)


def enable_telemetry(monkeypatch, tmp_path):
    telemetry.shutdown()
    monkeypatch.setattr(otel_metrics, "_trace_available", None)
    monkeypatch.setattr(otel_metrics, "_trace_module", trace)
    monkeypatch.setattr(
        trace,
        "get_tracer_provider",
        lambda: telemetry._tracer_provider or trace.NoOpTracerProvider(),
    )
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "trace")))
