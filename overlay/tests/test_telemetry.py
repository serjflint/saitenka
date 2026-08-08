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


def test_missing_extra_stays_disabled(monkeypatch, tmp_path):
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

    trace_path = telemetry.latest_trace(export)  # timestamped per-session file
    assert trace_path is not None and trace_path.exists()
    data = json.loads(trace_path.read_text())
    assert any(e["name"] == "load_deps_async" for e in data["traceEvents"])


def test_sample_counters_reads_gil_enabled_and_dropped_spans(tmp_path):
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    values = telemetry._sample_counters()
    assert values["runtime.gil_enabled"] in {0.0, 1.0}
    assert values["telemetry.dropped_spans"] == 0.0


def test_sample_counters_exports_only_spanless_histogram_summaries(tmp_path):
    # block_cache.rendered_px (band pixel height) has NO span — its summary is its only trace
    # representation, so it IS exported. render.duration_ms is span-backed — its percentiles are
    # derivable from the spans, so exporting the series too is pure duplication and must NOT happen.
    from overlay import otel_metrics

    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    for px in (64, 128, 256):
        otel_metrics.block_rendered_px.record(px)
    otel_metrics.render_duration_ms.record(5.0)
    values = telemetry._sample_counters()
    # spanless → exported
    assert values["block_cache.rendered_px.count"] == 3.0
    assert values["block_cache.rendered_px.max"] == 256
    assert "block_cache.rendered_px.p95" in values
    # span-backed → NOT exported (the span carries it)
    assert "render.duration_ms.p95" not in values
    assert "render.duration_ms.count" not in values
    # a scalar counter still exports as its bare value, no per-stat suffixes
    assert "panel_cache.hits.count" not in values


def test_sample_counters_has_no_otel_counters_when_not_configured():
    # snapshot() is empty without providers, so no OTel counter keys — but the process-global RSS
    # gauge and the dropped-span count are read directly and always present.
    values = telemetry._sample_counters()
    assert values["telemetry.dropped_spans"] == 0.0
    assert "process.rss_mb" in values
    assert "runtime.gil_enabled" not in values  # OTel instrument, needs configure()


def test_sample_counters_includes_registered_state_gauges(monkeypatch):
    # A Reader registers cache-size gauges via set_gauge_provider; the interval sampler folds them in
    # alongside RSS. Provider values pass through verbatim, RSS from perf.
    from overlay.app import perf

    monkeypatch.setattr(perf, "rss_mb", lambda: 123.5)
    telemetry.set_gauge_provider(lambda: {"panel_cache.size": 4.0, "dict_cache.size": 9.0})
    try:
        values = telemetry._sample_counters()
    finally:
        telemetry.set_gauge_provider(None)
    assert values["process.rss_mb"] == 123.5
    assert values["panel_cache.size"] == 4.0
    assert values["dict_cache.size"] == 9.0


def test_gauge_provider_exception_never_breaks_a_sample():
    def _boom():
        raise RuntimeError("cache read failed")

    telemetry.set_gauge_provider(_boom)
    try:
        values = telemetry._sample_counters()  # must not raise
    finally:
        telemetry.set_gauge_provider(None)
    assert values["telemetry.dropped_spans"] == 0.0  # the rest of the sample still lands


def test_configure_writes_counter_tracks_into_the_trace_file(tmp_path):
    """End-to-end: configure() wires a CounterSampler that periodically appends CTF counter (ph: C)
    events to the same trace.json the spans go into — verifies the whole pipeline, not just that
    _sample_counters() returns the right dict. Uses the sampler's real (1s) interval, so this test
    is allowed to take a couple of seconds."""
    import json
    import time as time_mod

    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))

    for _ in range(60):
        trace_path = telemetry.latest_trace(export)  # timestamped per-session file
        if trace_path is not None and trace_path.exists():
            data = json.loads(trace_path.read_text())
            if any(e["ph"] == "C" for e in data["traceEvents"]):
                break
        time_mod.sleep(0.1)
    else:
        raise AssertionError("no counter event landed in the trace within 6s")

    counter_names = {e["name"] for e in data["traceEvents"] if e["ph"] == "C"}
    assert "runtime.gil_enabled" in counter_names
    assert "telemetry.dropped_spans" in counter_names


def test_trace_rotation_prunes_oldest_and_latest_trace_picks_newest(tmp_path):
    """Per-session traces are timestamped; latest_trace serves the newest (for report/doctor/status),
    and _rotate_traces keeps the newest keep-1 so the incoming session brings the total to keep."""
    for ts in ("20260101-000001", "20260101-000002", "20260101-000003"):
        (tmp_path / f"trace-{ts}.json").write_text("{}")
    assert telemetry.latest_trace(tmp_path).name == "trace-20260101-000003.json"
    telemetry._rotate_traces(tmp_path, keep=2)  # keep newest 1 (a new session will make 2)
    assert sorted(p.name for p in tmp_path.glob("trace-*.json")) == ["trace-20260101-000003.json"]


def test_latest_trace_is_none_when_no_trace_yet(tmp_path):
    assert telemetry.latest_trace(tmp_path) is None


def test_active_gate_defaults_off_and_toggles():
    gate = telemetry.ActiveGate()
    assert bool(gate) is False
    gate.set(value=True)
    assert bool(gate) is True
    gate.set(value=False)
    assert bool(gate) is False
