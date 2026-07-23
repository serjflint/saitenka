"""Tests for overlay.app.otel_metrics: instrument registration + pull-based snapshot (Stage 7)."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from overlay.app import otel_metrics


@pytest.fixture
def registered():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    yield
    otel_metrics.unregister()
    provider.shutdown()


def test_snapshot_empty_before_register():
    assert otel_metrics.snapshot() == {}


def test_snapshot_reports_counter_value(registered):
    otel_metrics.panel_cache_hits.add(3)
    otel_metrics.panel_cache_hits.add(2)
    snap = otel_metrics.snapshot()
    assert snap["saitenka.panel_cache.hits"]["value"] == 5


def test_snapshot_reports_histogram_percentiles(registered):
    for ms in (1, 2, 5, 10, 50, 100, 500):
        otel_metrics.render_duration_ms.record(ms)
    snap = otel_metrics.snapshot()
    hist = snap["saitenka.render.duration_ms"]
    assert hist["count"] == 7
    assert hist["p50"] is not None
    assert hist["p95"] is not None
    assert hist["p99"] is not None
    assert hist["p50"] <= hist["p95"] <= hist["p99"]


def test_snapshot_reports_gil_enabled_gauge(registered):
    snap = otel_metrics.snapshot()
    assert snap["saitenka.runtime.gil_enabled"]["value"] in (0, 1)


def test_unregister_resets_instruments_to_none(registered):
    assert otel_metrics.render_duration_ms is not None
    otel_metrics.unregister()
    assert otel_metrics.render_duration_ms is None
    assert otel_metrics.snapshot() == {}


def test_percentiles_helper_on_empty_histogram():
    assert otel_metrics._percentiles([], [], 0.0, 0) == {"p50": None, "p95": None, "p99": None}


def test_timed_is_a_noop_when_histogram_is_none():
    """Every Stage 8 call site wraps with `otel_metrics.timed(otel_metrics.<hist>)` unconditionally
    — this must be safe when telemetry is disabled (the module attribute is None)."""
    ran = False
    with otel_metrics.timed(None):
        ran = True
    assert ran


def test_timed_records_duration_and_attributes(registered):
    with otel_metrics.timed(otel_metrics.dict_sql_duration_ms, dict="Jitendex"):
        pass
    snap = otel_metrics.snapshot()
    assert snap["saitenka.dict_sql.duration_ms"]["count"] == 1


def test_timed_records_even_on_exception(registered):
    with pytest.raises(ValueError), otel_metrics.timed(otel_metrics.render_duration_ms):
        raise ValueError("boom")
    snap = otel_metrics.snapshot()
    assert snap["saitenka.render.duration_ms"]["count"] == 1


def test_traced_runs_the_wrapped_block_when_opentelemetry_is_unimportable(monkeypatch):
    """Regression: controller.load_deps_async used to do a bare `from opentelemetry import trace`
    directly, which crashed on any install without the `observability` extra (found via live
    end-to-end testing — the dev/test env always has the extra via `[full]`, so no test caught it).
    traced() must run the wrapped block regardless of whether opentelemetry is importable."""
    real_import = __import__

    def _fake_import(name, *a, **kw):
        if name == "opentelemetry":
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    ran = False
    with otel_metrics.traced("test-span"):
        ran = True
    assert ran


def test_traced_creates_a_real_span_when_telemetry_is_configured(registered):
    with otel_metrics.traced("my-span", foo="bar"):
        pass
    # the fixture's provider has no exporter attached — this just proves start_as_current_span
    # didn't raise and the block ran; span content is covered end-to-end in test_telemetry.py.
