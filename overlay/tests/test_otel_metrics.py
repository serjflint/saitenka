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
