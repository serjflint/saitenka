"""Pull-based metric instruments: registered once when telemetry is configured, read on demand via
:func:`snapshot` — no periodic push, so metrics cost nothing until something actually inspects them
(``doctor``, a test, a future ``report`` bundle).

Instrument handles are module globals, ``None`` until :func:`register` runs (i.e. until telemetry is
enabled) — call sites must null-check before recording, the same pattern the rest of the codebase
already uses for optional collaborators. Low-cardinality labels only: dict *name* is fine,
per-word/per-entry is not (unbounded label cardinality is an OTel/Prometheus anti-pattern).
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import ModuleType

    from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricsData

_lock = threading.Lock()
_reader: InMemoryMetricReader | None = None

# Histograms — render/upload/hit-test/dict-sql/ipc-roundtrip/sub-seek, all in milliseconds.
render_duration_ms: Histogram | None = None
# Pixel height of one materialised block — the render-budget signal. A def body is one block, so this
# distribution's tail exposes the coarse-block jank (a single reach rasterises the whole body).
block_rendered_px: Histogram | None = None
upload_duration_ms: Histogram | None = None
hit_test_duration_ms: Histogram | None = None
dict_sql_duration_ms: Histogram | None = None
ipc_roundtrip_ms: Histogram | None = None
sub_seek_duration_ms: Histogram | None = None
# Seek-to-paint chain: cue_redraw wraps set_subtitle end-to-end (tokenize/score/render/upload);
# subtitle_render isolates the PIL render_subtitle() call inside it; sub_text_reconcile wraps the
# poll-loop's mpv-driven redraw (native sub-seek / normal cue advance), sibling to sub_nav's own
# sub_seek span for the instant-nav (Alt+←/→/↓) path.
cue_redraw_duration_ms: Histogram | None = None
subtitle_render_duration_ms: Histogram | None = None
sub_text_reconcile_duration_ms: Histogram | None = None
# Scroll-input → redraw-finished chain: wraps controller._scroll_tip end-to-end (banded/blit
# re-render + OSD upload) for one wheel tick or TIP_UP/DOWN keypress — its duration IS the
# scroll-to-photon latency a user would feel as stutter.
scroll_frame_duration_ms: Histogram | None = None
# Hover → tooltip-drawn latency: wraps show_tooltip end-to-end (panel_for head render + place +
# blit), the headline "does the tip feel instant?" number. Labeled kind=cold|warm — a cold hover
# builds the panel, a warm one is a cache hit + upload. Symmetric with scroll_frame/sub_seek, which
# already have their own end-to-end wrapper; the base-tooltip show was the one hot path that didn't.
show_tooltip_duration_ms: Histogram | None = None

# Counters.
panel_cache_hits: Counter | None = None
panel_cache_misses: Counter | None = None
panel_cache_evictions: Counter | None = (
    None  # LRU entries dropped — thrash driver under a small cap
)
dict_cache_hits: Counter | None = None
dict_cache_misses: Counter | None = None
# Per-block pixel cache (WindowedPanel._blocks), the layer under panel_cache: a miss rasterises the
# block, a hit reuses a still-retained one, an eviction drops it (O(viewport) retention → a re-scroll
# re-renders). The jank lives here, so it gets its own counters distinct from the per-word panel_cache.
block_cache_hits: Counter | None = None
block_cache_misses: Counter | None = None
block_cache_evictions: Counter | None = None
dropped_telemetry_spans: Counter | None = None
cold_first_paint_overshoot: Counter | None = None
osd_paused_draw: Counter | None = (
    None  # overlay draws that landed while paused (the #8172 bug window)
)
osd_paused_nudge: Counter | None = None  # paused-OSD re-flushes issued to un-throttle mpv
scroll_frame_jank: Counter | None = None  # scroll frames slower than SCROLL_JANK_THRESHOLD_MS

# ~one frame at 60Hz. The tail (p95/p99 jank-frame rate), not the mean, is what a user perceives
# as scroll stutter — see scroll_frame_jank.
SCROLL_JANK_THRESHOLD_MS = 16.0

# A cold hover slower than this feels laggy (the perceptible-lag threshold; well above one frame
# because a cold paint legitimately builds the panel). cold_first_paint_overshoot counts base-tooltip
# shows that a viewport-first head render was supposed to keep under it but didn't — see show_tooltip.
COLD_FIRST_PAINT_BUDGET_MS = 100.0

# Gauges (prefetch queue depth is push-updated by the caller; gil_enabled is observed on read).
prefetch_queue_depth: UpDownCounter | None = None

_ALL_HISTOGRAM_NAMES = (
    "saitenka.render.duration_ms",
    "saitenka.upload.duration_ms",
    "saitenka.hit_test.duration_ms",
    "saitenka.dict_sql.duration_ms",
    "saitenka.ipc.roundtrip_ms",
    "saitenka.sub_seek.duration_ms",
    "saitenka.cue_redraw.duration_ms",
    "saitenka.subtitle_render.duration_ms",
    "saitenka.sub_text_reconcile.duration_ms",
    "saitenka.scroll_frame.duration_ms",
    "saitenka.show_tooltip.duration_ms",
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
#: unavailable (the telemetry extra isn't installed — this can't change at runtime, so caching
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
    `opentelemetry` import attempt beyond the first) when the ``telemetry`` extra isn't
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
        # Per-thread CPU time across the span. wall (the span's dur) ≫ cpu_ms ⇒ the thread was
        # descheduled inside the span — GIL contention, a lock, or I/O wait — not doing work. This is
        # what disambiguates a genuinely-slow span from one merely stalled behind other threads: the
        # startup dep-load's freq-dict span reads ~470ms wall but ~290ms cpu once fugashi re-enables
        # the GIL, so ~180ms of it is contention, not freq work (invisible without this).
        cpu0 = time.thread_time()
        try:
            yield
        finally:
            span.set_attribute("cpu_ms", round((time.thread_time() - cpu0) * 1000.0, 3))


@contextmanager
def instrumented(histogram: Histogram | None, span_name: str, **attributes: str) -> Generator[None]:
    """:func:`traced` + :func:`timed` together — a span AND a histogram sample from the same block,
    for anchors where both a live percentile and a visible Perfetto timeline entry are useful.
    Deliberately NOT used at every anchor: a very-high-frequency call site (e.g. the mpv IPC
    round-trip, called on effectively every poll tick) would flood trace.json with spans — it stays
    on :func:`timed` alone."""
    with traced(span_name, **attributes), timed(histogram, **attributes):
        yield


@contextmanager
def instrumented_jank(
    histogram: Histogram | None,
    jank_counter: Counter | None,
    jank_threshold_ms: float,
    span_name: str,
    **attributes: str,
) -> Generator[None]:
    """:func:`instrumented` plus a jank counter bump when the block runs past *jank_threshold_ms* —
    for interactive paths (e.g. scroll) where the tail, not the mean, is what a user perceives as
    stutter. Both instruments share one timer so a jank frame's duration always matches what the
    histogram recorded for it."""
    if histogram is None and jank_counter is None:
        with traced(span_name, **attributes):
            yield
        return
    start = time.perf_counter()
    with traced(span_name, **attributes):
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if histogram is not None:
                histogram.record(elapsed_ms, attributes or None)
            if jank_counter is not None and elapsed_ms > jank_threshold_ms:
                jank_counter.add(1, attributes or None)


def _gil_enabled_callback(_options):
    from opentelemetry.metrics import Observation

    yield Observation(1 if sys._is_gil_enabled() else 0)


def register(reader: InMemoryMetricReader, meter: Meter) -> None:
    """Create every instrument against *meter* (whose provider is wired to *reader*). Idempotent
    isn't needed here — :func:`overlay.app.telemetry.configure` already guards against double-init."""
    global _reader
    global render_duration_ms, upload_duration_ms, hit_test_duration_ms
    global dict_sql_duration_ms, ipc_roundtrip_ms, sub_seek_duration_ms
    global cue_redraw_duration_ms, subtitle_render_duration_ms, sub_text_reconcile_duration_ms
    global scroll_frame_duration_ms, show_tooltip_duration_ms
    global panel_cache_hits, panel_cache_misses, panel_cache_evictions
    global dict_cache_hits, dict_cache_misses
    global block_rendered_px, block_cache_hits, block_cache_misses, block_cache_evictions
    global dropped_telemetry_spans, cold_first_paint_overshoot, prefetch_queue_depth
    global osd_paused_draw, osd_paused_nudge, scroll_frame_jank

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
        cue_redraw_duration_ms = meter.create_histogram(
            "saitenka.cue_redraw.duration_ms",
            unit="ms",
            description="set_subtitle end-to-end: tokenize/score/render/upload for one cue",
        )
        subtitle_render_duration_ms = meter.create_histogram(
            "saitenka.subtitle_render.duration_ms",
            unit="ms",
            description="render_subtitle() PIL layout+draw time for one cue",
        )
        sub_text_reconcile_duration_ms = meter.create_histogram(
            "saitenka.sub_text_reconcile.duration_ms",
            unit="ms",
            description="poll-loop redraw latency for an mpv-driven sub-text change (native "
            "sub-seek / normal cue advance), sibling to sub_seek for the instant-nav path",
        )
        scroll_frame_duration_ms = meter.create_histogram(
            "saitenka.scroll_frame.duration_ms",
            unit="ms",
            description="scroll-input to redraw-finished latency for one wheel tick / TIP_UP-DOWN",
        )
        show_tooltip_duration_ms = meter.create_histogram(
            "saitenka.show_tooltip.duration_ms",
            unit="ms",
            description="hover to base-tooltip-drawn latency (labeled kind=cold|warm)",
        )
        panel_cache_hits = meter.create_counter("saitenka.panel_cache.hits")
        panel_cache_misses = meter.create_counter("saitenka.panel_cache.misses")
        panel_cache_evictions = meter.create_counter(
            "saitenka.panel_cache.evictions", description="LRU panels dropped over the cap"
        )
        dict_cache_hits = meter.create_counter("saitenka.dict_cache.hits")
        dict_cache_misses = meter.create_counter("saitenka.dict_cache.misses")
        block_rendered_px = meter.create_histogram(
            "saitenka.block_cache.rendered_px", description="pixel height of one materialised block"
        )
        block_cache_hits = meter.create_counter("saitenka.block_cache.hits")
        block_cache_misses = meter.create_counter(
            "saitenka.block_cache.misses", description="blocks rasterised on demand (a render)"
        )
        block_cache_evictions = meter.create_counter(
            "saitenka.block_cache.evictions", description="retained block pixels dropped"
        )
        dropped_telemetry_spans = meter.create_counter("saitenka.telemetry.dropped_spans")
        cold_first_paint_overshoot = meter.create_counter(
            "saitenka.render.cold_first_paint_overshoot"
        )
        osd_paused_draw = meter.create_counter(
            "saitenka.osd.paused_draw", description="overlay draws that landed while mpv was paused"
        )
        osd_paused_nudge = meter.create_counter(
            "saitenka.osd.paused_nudge",
            description="paused-OSD re-flushes issued (mpv #8172 workaround)",
        )
        scroll_frame_jank = meter.create_counter(
            "saitenka.scroll_frame.jank",
            description=f"scroll frames slower than {SCROLL_JANK_THRESHOLD_MS:.0f}ms",
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
    global cue_redraw_duration_ms, subtitle_render_duration_ms, sub_text_reconcile_duration_ms
    global scroll_frame_duration_ms, show_tooltip_duration_ms
    global panel_cache_hits, panel_cache_misses, panel_cache_evictions
    global dict_cache_hits, dict_cache_misses
    global block_rendered_px, block_cache_hits, block_cache_misses, block_cache_evictions
    global dropped_telemetry_spans, cold_first_paint_overshoot, prefetch_queue_depth
    global osd_paused_draw, osd_paused_nudge, scroll_frame_jank

    with _lock:
        _reader = None
        render_duration_ms = None
        upload_duration_ms = None
        hit_test_duration_ms = None
        dict_sql_duration_ms = None
        ipc_roundtrip_ms = None
        sub_seek_duration_ms = None
        cue_redraw_duration_ms = None
        subtitle_render_duration_ms = None
        sub_text_reconcile_duration_ms = None
        scroll_frame_duration_ms = None
        show_tooltip_duration_ms = None
        panel_cache_hits = None
        panel_cache_misses = None
        panel_cache_evictions = None
        dict_cache_hits = None
        dict_cache_misses = None
        block_rendered_px = None
        block_cache_hits = None
        block_cache_misses = None
        block_cache_evictions = None
        dropped_telemetry_spans = None
        cold_first_paint_overshoot = None
        osd_paused_draw = None
        osd_paused_nudge = None
        scroll_frame_jank = None
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
    """Merge every data point into one summary — an instrument recorded with per-label attributes
    (e.g. `dict_sql_duration_ms`'s `dict=<title>`) fans out into one point PER label, not one point
    total. A previous version read only `points[-1]` on the (false, for labeled instruments)
    assumption of a single track, silently reporting one label's count/sum instead of the sum across
    all of them — found via cross-checking this snapshot against the CTF trace.json span totals for
    the same run (~9-11x undercount, one point per dictionary title)."""
    data = metric.data
    points = getattr(data, "data_points", [])
    if not points:
        return {}
    if hasattr(points[0], "bucket_counts"):  # HistogramDataPoint(s) — same bucket bounds per metric
        count = sum(p.count for p in points)
        total = sum(p.sum for p in points)
        dp_max = max(p.max for p in points)
        bucket_counts = [sum(b) for b in zip(*(p.bucket_counts for p in points), strict=True)]
        return {
            "count": count,
            "sum": total,
            **_percentiles(bucket_counts, points[0].explicit_bounds, dp_max, count),
        }
    return {"value": sum(p.value for p in points)}
