"""Tests for overlay.otel_metrics: instrument registration + pull-based snapshot (Stage 7)."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from overlay import otel_metrics


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


@pytest.mark.usefixtures("registered")
def test_snapshot_reports_counter_value():
    otel_metrics.panel_cache_hits.add(3)
    otel_metrics.panel_cache_hits.add(2)
    snap = otel_metrics.snapshot()
    assert snap["saitenka.panel_cache.hits"]["value"] == 5


@pytest.mark.usefixtures("registered")
def test_snapshot_reports_histogram_percentiles():
    for ms in (1, 2, 5, 10, 50, 100, 500):
        otel_metrics.render_duration_ms.record(ms)
    snap = otel_metrics.snapshot()
    hist = snap["saitenka.render.duration_ms"]
    assert hist["count"] == 7
    assert hist["p50"] is not None
    assert hist["p95"] is not None
    assert hist["p99"] is not None
    assert hist["p50"] <= hist["p95"] <= hist["p99"]


@pytest.mark.usefixtures("registered")
def test_instrumented_emit_span_false_still_records_histogram():
    # The sampled path (emit_span=False) drops the trace span but must still record the histogram —
    # the percentile is what survives, so aggregate SQL/IPC cost stays visible even when unsampled.
    with otel_metrics.instrumented(otel_metrics.dict_sql_duration_ms, "dict_sql", emit_span=False):
        pass
    assert otel_metrics.snapshot()["saitenka.dict_sql.duration_ms"]["count"] == 1


@pytest.mark.usefixtures("registered")
def test_snapshot_histogram_reports_exact_max():
    # p50/p95/p99 are bucket-bound estimates; max is the exact largest sample — the band-size tail
    # the trace consumer needs, so it's summarized alongside the percentiles.
    for px in (64, 128, 200, 256):
        otel_metrics.block_rendered_px.record(px)
    hist = otel_metrics.snapshot()["saitenka.block_cache.rendered_px"]
    assert hist["max"] == 256
    assert hist["count"] == 4


@pytest.mark.usefixtures("registered")
def test_snapshot_reports_gil_enabled_gauge():
    snap = otel_metrics.snapshot()
    assert snap["saitenka.runtime.gil_enabled"]["value"] in (0, 1)


@pytest.mark.usefixtures("registered")
def test_scroll_frame_jank_counter_bumps_only_past_threshold():
    # The --scroll-jank benchmark's headline signal (a frame over the display budget = visible stutter):
    # instrumented_jank must bump saitenka.scroll_frame.jank ONLY when the block runs past the
    # threshold, while the histogram always records. Threshold 0.0 → any real elapsed is "janky";
    # a huge threshold → the same fast frame is not — deterministic, no sleep.
    with otel_metrics.instrumented_jank(
        otel_metrics.scroll_frame_duration_ms, otel_metrics.scroll_frame_jank, 0.0, "scroll_frame"
    ):
        pass
    with otel_metrics.instrumented_jank(
        otel_metrics.scroll_frame_duration_ms, otel_metrics.scroll_frame_jank, 1e9, "scroll_frame"
    ):
        pass
    snap = otel_metrics.snapshot()
    assert snap["saitenka.scroll_frame.jank"]["value"] == 1  # only the over-threshold frame
    assert snap["saitenka.scroll_frame.duration_ms"]["count"] == 2  # both frames still timed


@pytest.mark.usefixtures("registered")
def test_unregister_resets_instruments_to_none():
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


@pytest.mark.usefixtures("registered")
def test_timed_records_duration_and_attributes():
    with otel_metrics.timed(otel_metrics.dict_sql_duration_ms, dict="Jitendex"):
        pass
    snap = otel_metrics.snapshot()
    assert snap["saitenka.dict_sql.duration_ms"]["count"] == 1


@pytest.mark.usefixtures("registered")
def test_timed_records_even_on_exception():
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
        if name.startswith("opentelemetry"):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    # traced() memoizes the import resolution (a hot-path optimization) — reset it so this test's
    # patched __import__ actually gets exercised instead of an earlier test's cached success.
    monkeypatch.setattr(otel_metrics, "_trace_available", None)
    monkeypatch.setattr(otel_metrics, "_trace_module", None)
    ran = False
    with otel_metrics.traced("test-span"):
        ran = True
    assert ran


@pytest.mark.usefixtures("registered")
def test_traced_creates_a_real_span_when_telemetry_is_configured():
    with otel_metrics.traced("my-span", foo="bar"):
        pass
    # the fixture's provider has no exporter attached — this just proves start_as_current_span
    # didn't raise and the block ran; span content is covered end-to-end in test_telemetry.py.


class _FakeSpan:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeTracer:
    def __init__(self):
        self.spans = []

    def start_as_current_span(self, _name):
        span = _FakeSpan()
        self.spans.append(span)
        return span


class _FakeTraceModule:
    def __init__(self):
        self.tracer = _FakeTracer()

    def get_tracer(self, _name):
        return self.tracer


def test_traced_stamps_cpu_ms_so_wall_vs_cpu_reveals_a_stall(monkeypatch):
    """wall (the span dur) ≫ cpu_ms ⇒ the thread was descheduled (GIL contention / lock / I/O),
    not working — the one signal that told genuinely-CPU-bound freq load apart from GIL-stalled
    startup. A fake trace module keeps this off OTel's set-once global provider."""
    fake = _FakeTraceModule()
    monkeypatch.setattr(otel_metrics, "_trace_available", True)
    monkeypatch.setattr(otel_metrics, "_trace_module", fake)
    with otel_metrics.traced("x"):
        pass
    attrs = fake.tracer.spans[0].attributes
    assert "cpu_ms" in attrs
    assert isinstance(attrs["cpu_ms"], float)


def test_traced_stamps_the_real_native_thread_id(monkeypatch):
    """otel_export._span_to_ctf_event reads this attribute back for the CTF "tid" field — without
    it, independently-started spans (no parent-child relationship, so different random trace_ids)
    scatter across a different synthetic "thread" track each in Perfetto instead of grouping by the
    actual Python thread that ran them. A fake trace module here keeps this deterministic — no
    dependency on OTel's global-provider-set-once ordering across the test session."""
    import threading

    fake = _FakeTraceModule()
    monkeypatch.setattr(otel_metrics, "_trace_available", True)
    monkeypatch.setattr(otel_metrics, "_trace_module", fake)
    with otel_metrics.traced("x"):
        pass
    assert fake.tracer.spans[0].attributes["thread.id"] == threading.get_native_id()


def test_instrumented_is_a_noop_when_histogram_is_none():
    """The Stage 8 anchors that want both a span and a histogram (render, dict_sql, upload,
    sub_seek, hit_test) use instrumented() — must stay safe with telemetry off."""
    ran = False
    with otel_metrics.instrumented(None, "some-span"):
        ran = True
    assert ran


@pytest.mark.usefixtures("registered")
def test_instrumented_records_both_the_histogram_and_a_span():
    with otel_metrics.instrumented(otel_metrics.render_duration_ms, "render", dict="X"):
        pass
    snap = otel_metrics.snapshot()
    assert snap["saitenka.render.duration_ms"]["count"] == 1


@pytest.mark.usefixtures("registered")
def test_show_tooltip_histogram_records_with_a_kind_label():
    otel_metrics.show_tooltip_duration_ms.record(30.0, {"kind": "cold"})
    otel_metrics.show_tooltip_duration_ms.record(1.0, {"kind": "warm"})
    snap = otel_metrics.snapshot()
    assert snap["saitenka.show_tooltip.duration_ms"]["count"] == 2


@pytest.mark.usefixtures("registered")
def test_panel_cache_evictions_counter_is_registered_and_records():
    otel_metrics.panel_cache_evictions.add(4)
    snap = otel_metrics.snapshot()
    assert snap["saitenka.panel_cache.evictions"]["value"] == 4


@pytest.mark.usefixtures("registered")
def test_record_show_metrics_bumps_overshoot_only_for_a_cold_over_budget_show():
    """A cold show past the budget is a first-paint miss; a warm show over budget (or a cold show
    under it) is not — the counter must fire on exactly the first case so the signal isn't polluted."""
    from types import SimpleNamespace

    from overlay.app import tooltip

    over = otel_metrics.COLD_FIRST_PAINT_BUDGET_MS + 1
    under = otel_metrics.COLD_FIRST_PAINT_BUDGET_MS - 1
    tooltip._record_show_metrics(SimpleNamespace(_tip_show_cold=True), over)  # counts
    tooltip._record_show_metrics(SimpleNamespace(_tip_show_cold=False), over)  # warm → no count
    tooltip._record_show_metrics(
        SimpleNamespace(_tip_show_cold=True), under
    )  # fast cold → no count
    snap = otel_metrics.snapshot()
    assert snap["saitenka.render.cold_first_paint_overshoot"]["value"] == 1
    assert snap["saitenka.show_tooltip.duration_ms"]["count"] == 3  # every show recorded regardless


@pytest.mark.usefixtures("registered")
def test_snapshot_sums_a_labeled_histogram_across_every_label():
    """Regression: dict_sql_duration_ms is recorded with a per-dict `dict=<title>` attribute, which
    OTel fans out into one data point PER distinct label — `_summarize_metric` used to read only
    `points[-1]`, silently reporting one dict's count/sum instead of the total across all of them
    (found by cross-checking a live snapshot against the CTF trace.json span totals for the same
    run: ~9-11x undercount)."""
    otel_metrics.dict_sql_duration_ms.record(5, {"dict": "Jitendex"})
    otel_metrics.dict_sql_duration_ms.record(500, {"dict": "Daijisen"})
    otel_metrics.dict_sql_duration_ms.record(5, {"dict": "JMdict"})
    snap = otel_metrics.snapshot()
    hist = snap["saitenka.dict_sql.duration_ms"]
    assert hist["count"] == 3
    assert hist["sum"] == 510
