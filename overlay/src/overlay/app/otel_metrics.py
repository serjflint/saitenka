"""Pull-based metric instruments (Stage 7 of ``vibe/observability-plan.md``): registered once when
telemetry is configured, read on demand via :func:`snapshot` — no periodic push, so metrics cost
nothing until something actually inspects them (``doctor``, a test, a future ``report`` bundle).

Instrument handles are module globals, ``None`` until :func:`register` runs (i.e. until telemetry is
enabled) — call sites (Stage 8) must null-check before recording, the same pattern the rest of the
codebase already uses for optional collaborators. Low-cardinality labels only: dict *name* is fine,
per-word/per-entry is not (unbounded label cardinality is an OTel/Prometheus anti-pattern).
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricsData

_lock = threading.Lock()
_reader: InMemoryMetricReader | None = None

# Histograms — render/upload/hit-test/dict-sql/ipc-roundtrip/sub-seek, all in milliseconds.
render_duration_ms: Histogram | None = None
upload_duration_ms: Histogram | None = None
hit_test_duration_ms: Histogram | None = None
dict_sql_duration_ms: Histogram | None = None
ipc_roundtrip_ms: Histogram | None = None
sub_seek_duration_ms: Histogram | None = None

# Counters.
panel_cache_hits: Counter | None = None
panel_cache_misses: Counter | None = None
dict_cache_hits: Counter | None = None
dict_cache_misses: Counter | None = None
dropped_telemetry_spans: Counter | None = None
cold_first_paint_overshoot: Counter | None = None

# Gauges (prefetch queue depth is push-updated by the caller; gil_enabled is observed on read).
prefetch_queue_depth: UpDownCounter | None = None

_ALL_HISTOGRAM_NAMES = (
    "saitenka.render.duration_ms",
    "saitenka.upload.duration_ms",
    "saitenka.hit_test.duration_ms",
    "saitenka.dict_sql.duration_ms",
    "saitenka.ipc.roundtrip_ms",
    "saitenka.sub_seek.duration_ms",
)


@contextmanager
def timed(histogram: Histogram | None, **attributes: str) -> Generator[None]:
    """Record the wrapped block's duration (ms) into *histogram* — a no-op when it's ``None``
    (telemetry disabled or not yet configured), so every call site stays safe to wrap
    unconditionally. Pass the live module attribute (e.g. ``otel_metrics.dict_sql_duration_ms``),
    not a captured local — the attribute is re-read fresh each time the ``with`` block is entered,
    so it tracks configure()/shutdown() without the call site needing to care."""
    if histogram is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        histogram.record((time.perf_counter() - start) * 1000.0, attributes or None)


#: Memoized resolution of `opentelemetry.trace`: `None` = not yet checked, ``False`` = confirmed
#: unavailable (the observability extra isn't installed — this can't change at runtime, so caching
#: it forever is correct). Avoids re-attempting (and re-catching ImportError from) a failing import
#: on every single call from a hot call site like `traced()`.
_trace_available: bool | None = None
_trace_module: ModuleType | None = None


def _resolve_trace_module() -> ModuleType | None:
    global _trace_available, _trace_module
    if _trace_available is None:
        try:
            import opentelemetry.trace as _trace
        except ImportError:
            _trace_available = False
        else:
            _trace_available = True
            _trace_module = _trace
    return _trace_module if _trace_available else None


@contextmanager
def traced(name: str, **attributes: str) -> Generator[None]:
    """A real OTel span via the global tracer API — a no-op (not just an unrecorded span, but no
    `opentelemetry` import attempt beyond the first) when the ``observability`` extra isn't
    installed, so every call site stays safe to wrap unconditionally, same contract as :func:`timed`.
    When the extra IS installed but telemetry isn't configured, `trace.get_tracer()` itself returns
    OTel's built-in no-op tracer — cheap, not a crash.

    Bug this exists to prevent: an earlier direct ``from opentelemetry import trace`` at a call site
    crashed a background thread on any install without the extra — found via live end-to-end testing,
    not by any test in this suite (the dev/test env always has the extra installed via `[full]`)."""
    trace = _resolve_trace_module()
    if trace is None:
        yield
        return
    with trace.get_tracer("saitenka.overlay").start_as_current_span(name) as span:
        # otel_export._span_to_ctf_event reads this back for the CTF event's "tid" — without it,
        # every independently-started span (no parent) gets a random trace_id, and using THAT for
        # tid scatters unrelated spans across a different synthetic "thread" row each, defeating the
        # point of a timeline view (found by actually opening a real trace in Perfetto and looking).
        span.set_attribute("thread.id", threading.get_native_id())
        for k, v in attributes.items():
            span.set_attribute(k, v)
        yield


@contextmanager
def instrumented(histogram: Histogram | None, span_name: str, **attributes: str) -> Generator[None]:
    """:func:`traced` + :func:`timed` together — a span AND a histogram sample from the same block,
    for anchors where both a live percentile and a visible Perfetto timeline entry are useful.
    Deliberately NOT used at every anchor: a very-high-frequency call site (e.g. the mpv IPC
    round-trip, called on effectively every poll tick) would flood trace.json with spans — it stays
    on :func:`timed` alone. See the anchor list in ``vibe/observability-plan.md`` Stage 8 for which
    is which."""
    with traced(span_name, **attributes), timed(histogram, **attributes):
        yield


def _gil_enabled_callback(_options):
    from opentelemetry.metrics import Observation

    yield Observation(1 if sys._is_gil_enabled() else 0)


def register(reader: InMemoryMetricReader, meter: Meter) -> None:
    """Create every instrument against *meter* (whose provider is wired to *reader*). Idempotent
    isn't needed here — :func:`overlay.app.telemetry.configure` already guards against double-init."""
    global _reader
    global render_duration_ms, upload_duration_ms, hit_test_duration_ms
    global dict_sql_duration_ms, ipc_roundtrip_ms, sub_seek_duration_ms
    global panel_cache_hits, panel_cache_misses, dict_cache_hits, dict_cache_misses
    global dropped_telemetry_spans, cold_first_paint_overshoot, prefetch_queue_depth

    with _lock:
        _reader = reader
        render_duration_ms = meter.create_histogram(
            "saitenka.render.duration_ms", unit="ms", description="panel/flow render time"
        )
        upload_duration_ms = meter.create_histogram(
            "saitenka.upload.duration_ms", unit="ms", description="overlay BGRA upload time"
        )
        hit_test_duration_ms = meter.create_histogram(
            "saitenka.hit_test.duration_ms", unit="ms", description="per-tick hover hit-test time"
        )
        dict_sql_duration_ms = meter.create_histogram(
            "saitenka.dict_sql.duration_ms", unit="ms", description="dictionary SQLite lookup time"
        )
        ipc_roundtrip_ms = meter.create_histogram(
            "saitenka.ipc.roundtrip_ms", unit="ms", description="mpv IPC command round-trip time"
        )
        sub_seek_duration_ms = meter.create_histogram(
            "saitenka.sub_seek.duration_ms", unit="ms", description="subtitle-index seek time"
        )
        panel_cache_hits = meter.create_counter("saitenka.panel_cache.hits")
        panel_cache_misses = meter.create_counter("saitenka.panel_cache.misses")
        dict_cache_hits = meter.create_counter("saitenka.dict_cache.hits")
        dict_cache_misses = meter.create_counter("saitenka.dict_cache.misses")
        dropped_telemetry_spans = meter.create_counter("saitenka.telemetry.dropped_spans")
        cold_first_paint_overshoot = meter.create_counter(
            "saitenka.render.cold_first_paint_overshoot"
        )
        prefetch_queue_depth = meter.create_up_down_counter("saitenka.prefetch.queue_depth")
        meter.create_observable_gauge(
            "saitenka.runtime.gil_enabled",
            callbacks=[_gil_enabled_callback],
            description="1 if the GIL is enabled (0 = running free-threaded)",
        )


def unregister() -> None:
    global _reader
    global render_duration_ms, upload_duration_ms, hit_test_duration_ms
    global dict_sql_duration_ms, ipc_roundtrip_ms, sub_seek_duration_ms
    global panel_cache_hits, panel_cache_misses, dict_cache_hits, dict_cache_misses
    global dropped_telemetry_spans, cold_first_paint_overshoot, prefetch_queue_depth

    with _lock:
        _reader = None
        render_duration_ms = None
        upload_duration_ms = None
        hit_test_duration_ms = None
        dict_sql_duration_ms = None
        ipc_roundtrip_ms = None
        sub_seek_duration_ms = None
        panel_cache_hits = None
        panel_cache_misses = None
        dict_cache_hits = None
        dict_cache_misses = None
        dropped_telemetry_spans = None
        cold_first_paint_overshoot = None
        prefetch_queue_depth = None


def _percentiles(
    bucket_counts, explicit_bounds, dp_max, total, ps=(0.5, 0.95, 0.99)
) -> dict[str, float | None]:
    """Linear-interpolation-free percentile estimate: the bound of the first bucket whose
    cumulative count reaches ``p * total``. Coarser than true percentiles (bounded by the
    histogram's bucket boundaries), but matches what a live inspector needs — a ballpark, not a
    forensic replay."""
    if total == 0:
        return {f"p{int(p * 100)}": None for p in ps}
    bounds = [*explicit_bounds, dp_max]
    remaining = {p: p * total for p in ps}
    result: dict[str, float | None] = {}
    cum = 0
    for count, bound in zip(bucket_counts, bounds, strict=True):
        cum += count
        for p in list(remaining):
            if cum >= remaining[p]:
                result[f"p{int(p * 100)}"] = bound
                del remaining[p]
    for p in remaining:
        result[f"p{int(p * 100)}"] = dp_max
    return result


def snapshot() -> dict[str, dict[str, object]]:
    """A point-in-time read of every registered instrument. ``{}`` if telemetry isn't configured.
    Histograms summarize to p50/p95/p99 + count; counters/gauges report their latest value."""
    if _reader is None:
        return {}
    data: MetricsData | None = _reader.get_metrics_data()
    if data is None:
        return {}
    out: dict[str, dict[str, object]] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = _summarize_metric(metric)
    return out


def _summarize_metric(metric) -> dict[str, object]:
    data = metric.data
    points = getattr(data, "data_points", [])
    if not points:
        return {}
    point = points[-1]  # single-track instruments here (no per-label fan-out) — latest point
    if hasattr(point, "bucket_counts"):  # HistogramDataPoint
        return {
            "count": point.count,
            "sum": point.sum,
            **_percentiles(point.bucket_counts, point.explicit_bounds, point.max, point.count),
        }
    return {"value": point.value}
