"""Tests for overlay.app.telemetry: opt-in lifecycle, no-op-when-disabled, idempotence."""

from __future__ import annotations

import sys

import pytest

from overlay.app import telemetry
from overlay.app.config import TelemetryOptions


@pytest.fixture(autouse=True)
def _reset_providers():
    """configure()/shutdown() mutate module globals; isolate each test."""
    telemetry.shutdown()
    yield
    telemetry.shutdown()


def test_disabled_is_a_full_noop(tmp_path):
    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=False, export_dir=str(export)))
    assert telemetry.is_enabled() is False
    assert not export.exists()  # no directory created, no providers stood up


def test_enabled_stands_up_providers_and_creates_export_dir(tmp_path):
    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    assert telemetry.is_enabled() is True
    assert export.is_dir()


def test_configure_is_idempotent(tmp_path):
    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    from opentelemetry import trace

    first = trace.get_tracer_provider()
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    assert trace.get_tracer_provider() is first


def test_shutdown_resets_state(tmp_path):
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    assert telemetry.is_enabled() is True
    telemetry.shutdown()
    assert telemetry.is_enabled() is False
    telemetry.shutdown()  # a second call is a harmless no-op


def test_export_dir_defaults_to_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "cache_dir", lambda: tmp_path)
    assert telemetry.export_dir(TelemetryOptions()) == tmp_path / "telemetry"


def test_missing_extra_stays_disabled(monkeypatch, tmp_path, caplog):
    """Simulate the observability extra not being installed: opentelemetry imports fail inside
    configure(), and telemetry must stay fully off rather than raise."""
    real_import = __import__

    def _fake_import(name, *a, **kw):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr("builtins.__import__", _fake_import)

    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    assert telemetry.is_enabled() is False


def test_configure_turns_span_gate_on_so_traces_actually_get_captured(tmp_path):
    """Regression: an earlier version left span_gate off by default, which meant enabling telemetry
    produced logs + metrics but NEVER a trace file — nothing else in the codebase flips the gate.
    Found via a real end-to-end `run --demo-word` session, not a unit test."""
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    assert bool(telemetry.span_gate) is True


def test_shutdown_turns_span_gate_back_off(tmp_path):
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    telemetry.shutdown()
    assert bool(telemetry.span_gate) is False


def test_end_to_end_span_reaches_the_ctf_trace_file(tmp_path):
    """The full path a real session exercises: configure telemetry, start a span the way
    controller.load_deps_async does (`start_as_current_span`), shut down (which flushes), and
    confirm an actual trace.json lands with that span in it — not just that the gate is on (the
    regression above), the whole pipe end to end.

    Uses the provider `configure()` just built directly (`telemetry._tracer_provider`), not the
    global `opentelemetry.trace.get_tracer()` API: OTel only allows the global provider to be set
    ONCE per process (by design — a real app calls `configure()` exactly once at startup, so this
    never bites production), and this test file's other tests already latched a provider globally
    earlier in the same pytest session."""
    import json

    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    tp = telemetry._tracer_provider
    assert tp is not None
    with tp.get_tracer(__name__).start_as_current_span("load_deps_async"):
        pass
    telemetry.shutdown()

    trace_path = export / "trace.json"
    assert trace_path.exists()
    data = json.loads(trace_path.read_text())
    assert any(e["name"] == "load_deps_async" for e in data["traceEvents"])


def test_sample_counters_reads_gil_enabled_and_dropped_spans(tmp_path):
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    values = telemetry._sample_counters()
    assert values["runtime.gil_enabled"] in (0.0, 1.0)
    assert values["telemetry.dropped_spans"] == 0.0


def test_sample_counters_empty_when_not_configured():
    assert telemetry._sample_counters() == {"telemetry.dropped_spans": 0.0}


def test_configure_writes_counter_tracks_into_the_trace_file(tmp_path):
    """End-to-end: configure() wires a CounterSampler that periodically appends CTF counter (ph: C)
    events to the same trace.json the spans go into — verifies the whole pipeline, not just that
    _sample_counters() returns the right dict. Uses the sampler's real (1s) interval, so this test
    is allowed to take a couple of seconds."""
    import json
    import time as time_mod

    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    trace_path = export / "trace.json"

    for _ in range(60):
        if trace_path.exists():
            data = json.loads(trace_path.read_text())
            if any(e["ph"] == "C" for e in data["traceEvents"]):
                break
        time_mod.sleep(0.1)
    else:
        raise AssertionError("no counter event landed in trace.json within 6s")

    counter_names = {e["name"] for e in data["traceEvents"] if e["ph"] == "C"}
    assert "runtime.gil_enabled" in counter_names
    assert "telemetry.dropped_spans" in counter_names


def test_active_gate_defaults_off_and_toggles():
    gate = telemetry.ActiveGate()
    assert bool(gate) is False
    gate.set(True)
    assert bool(gate) is True
    gate.set(False)
    assert bool(gate) is False
