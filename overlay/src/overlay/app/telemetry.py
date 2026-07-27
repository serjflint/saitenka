"""OpenTelemetry tracer/meter provider lifecycle — fully opt-in, fully no-op when disabled.

The ``opentelemetry`` package (the ``telemetry`` extra) is imported lazily, only inside
:func:`configure`, and only once ``TelemetryOptions.enabled`` is true —
a default install pays zero import cost, and a config with telemetry off never touches the SDK at
all: no providers, no threads, no export directory created.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, final

from overlay.app.paths import cache_dir

#: Per-session trace rotation: each run writes its own ``trace-<timestamp>.json`` (a CTF doc is one
#: recording — appending sessions into one file overlays their clocks/thread-ids), and the newest N are
#: kept so history survives without unbounded growth. report/doctor/status read the newest via
#: :func:`latest_trace`.
_KEEP_TRACES = 10

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    from overlay.app.config import TelemetryOptions
    from overlay.app.otel_export import CTFSpanProcessor

log = logging.getLogger(__name__)


@final
class ActiveGate:
    """A cheap flag an inspector (``doctor``, a future runtime keybind) flips on to make a gated
    component start actually recording. Reading it when off is a single attribute load — no lock,
    matching mpv's "free when nobody is inspecting" model. Not a linearizable primitive: under
    free-threading a flip can be observed a beat late by another thread, which is fine for a
    sampling/recording toggle (worst case: one extra or one skipped span right at the flip)."""

    __slots__ = ("_on",)

    def __init__(self) -> None:
        self._on = False

    def __bool__(self) -> bool:
        return self._on

    def set(self, *, value: bool) -> None:
        self._on = value


_lock = threading.Lock()
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_span_processor: CTFSpanProcessor | None = None

#: Named counter/gauge instruments (see otel_metrics.register) sampled once per tick into the CTF
#: trace as graph tracks — curated, not "every instrument": duration histograms are visualized as
#: spans instead (that's what Perfetto's timeline is for), not as a value-over-time line.
_SAMPLED_COUNTERS = (
    "saitenka.runtime.gil_enabled",
    "saitenka.prefetch.queue_depth",
    "saitenka.panel_cache.hits",
    "saitenka.panel_cache.misses",
    "saitenka.panel_cache.evictions",
    "saitenka.dict_cache.hits",
    "saitenka.dict_cache.misses",
    "saitenka.osd.paused_draw",
    "saitenka.osd.paused_nudge",
)

#: Gates the span pipeline. Starts off (so it costs nothing before/without telemetry); `configure()`
#: turns it on as part of enabling telemetry — see the comment there. Exists as a separate switch
#: from `TelemetryOptions.enabled` for a future dynamic on/off (a doctor/keybind hook toggling
#: capture without a restart), not as a second "is telemetry on" gate a user has to know about.
span_gate = ActiveGate()


def export_dir(options: TelemetryOptions) -> Path:
    return Path(options.export_dir) if options.export_dir else cache_dir() / "telemetry"


def latest_trace(out_dir: Path) -> Path | None:
    """The most recent per-session CTF trace in *out_dir* (report/doctor/status read this), or None if
    none yet. Timestamped names sort chronologically, so the lexical max is the newest."""
    traces = sorted(out_dir.glob("trace-*.json"))
    return traces[-1] if traces else None


def _rotate_traces(out_dir: Path, keep: int) -> None:
    """Prune the oldest per-session traces before a new session starts, keeping the newest ``keep``-1
    so the session about to begin brings the total to at most ``keep``."""
    existing = sorted(out_dir.glob("trace-*.json"))
    for old in existing[: max(0, len(existing) - (keep - 1))]:
        try:
            old.unlink()
        except OSError:
            log.debug("could not prune old trace %s", old, exc_info=True)


def is_enabled() -> bool:
    """True once :func:`configure` has stood up live providers (i.e. telemetry is enabled AND the
    ``telemetry`` extra is installed)."""
    return _tracer_provider is not None


def dropped_span_count() -> int:
    return _span_processor.dropped_count if _span_processor is not None else 0


#: Live state-gauge provider (cache sizes) registered by the running Reader — see
#: ``set_gauge_provider``. Sampled on the writer thread's interval alongside the OTel counters, NOT
#: an OTel instrument itself: the values it reports (panel/dict cache occupancy) are the Reader's own
#: live state, cheaper to read directly than to mirror into an observable gauge the Reader would have
#: to keep pushing. RSS is read straight from ``perf`` (process-global, no Reader needed).
_gauge_provider: Callable[[], dict[str, float]] | None = None


def set_gauge_provider(fn: Callable[[], dict[str, float]] | None) -> None:
    """Register (or clear, with ``None``) the state-gauge source sampled each interval. The Reader
    registers its cache-size gauges here in ``run`` and clears them in ``close``."""
    global _gauge_provider
    _gauge_provider = fn


def _sample_counters() -> dict[str, float]:
    """The writer thread's ``sample_fn``: the curated instrument values from the last
    ``otel_metrics.snapshot()``, plus the span queue's live dropped-count (not itself an OTel
    instrument — reading straight from the processor avoids double-bookkeeping a Counter that would
    need incrementing from two different places), plus process RSS and any registered state gauges
    (cache sizes). All sampled on the same ~1s cadence so they graph time-correlated with the spans."""
    from overlay import otel_metrics
    from overlay.app import perf

    snap = otel_metrics.snapshot()
    out: dict[str, float] = {}
    for name in _SAMPLED_COUNTERS:
        value = snap.get(name, {}).get("value")
        if isinstance(value, int | float):
            out[name.removeprefix("saitenka.")] = float(value)
    out["telemetry.dropped_spans"] = float(dropped_span_count())
    rss = perf.rss_mb()
    if rss is not None:
        out["process.rss_mb"] = rss
    if _gauge_provider is not None:
        try:
            out.update(_gauge_provider())
        except Exception:
            log.debug("gauge provider failed", exc_info=True)
    return out


def configure(options: TelemetryOptions) -> None:
    """Idempotent: a no-op if disabled, if the extra isn't installed, or if already configured."""
    global _tracer_provider, _meter_provider, _span_processor
    if not options.enabled:
        return
    with _lock:
        if _tracer_provider is not None:
            return
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import InMemoryMetricReader
            from opentelemetry.sdk.trace import TracerProvider
        except ImportError:
            log.warning(
                "telemetry.enabled=true but the 'telemetry' extra isn't installed "
                "(uv tool install --reinstall 'saitenka-overlay[telemetry]') — telemetry stays off"
            )
            return

        from overlay.app.otel_export import CTFSpanProcessor
        from overlay.otel_metrics import register as register_metrics

        out_dir = export_dir(options)
        out_dir.mkdir(parents=True, exist_ok=True)
        _rotate_traces(
            out_dir, _KEEP_TRACES
        )  # prune old sessions, then start THIS one's own fresh file
        # UTC (not local): latest_trace/_rotate_traces rely on lexical sort == chronological order, which
        # local time breaks at DST fall-back (01:00-02:00 repeats). The trailing Z marks it unambiguous.
        trace_path = out_dir / f"trace-{time.strftime('%Y%m%d-%H%M%SZ', time.gmtime())}.json"

        tp = TracerProvider()
        # One processor owns the queue, the writer thread, and the file. sample_fn wires the curated
        # counters (below) as CTF "counter" tracks in the SAME trace file the spans go into — Perfetto
        # graphs them next to the spans, time-correlated, no separate metrics-visualization stack.
        processor = CTFSpanProcessor(trace_path, span_gate, sample_fn=_sample_counters)
        tp.add_span_processor(processor)
        # TelemetryOptions.enabled is the actual opt-in switch; the gate defaulting off would mean
        # enabling telemetry produces logs + metrics but NO trace ever, since nothing else flips it —
        # confirmed live via a real `run --demo-word` session before this line was added. The gate
        # stays around for a future dynamic on/off (a doctor/keybind hook toggling capture without a
        # restart), not as a second "are we actually enabled" switch.
        span_gate.set(value=True)
        reader = InMemoryMetricReader()  # pull-based: read on demand via otel_metrics.snapshot()
        mp = MeterProvider(metric_readers=[reader])
        trace.set_tracer_provider(tp)
        metrics.set_meter_provider(mp)
        register_metrics(reader, mp.get_meter("saitenka.overlay"))
        _tracer_provider = tp
        _meter_provider = mp
        _span_processor = processor
        log.info("telemetry enabled: export_dir=%s", out_dir)


def shutdown() -> None:
    """Flush + tear down the providers. Safe to call even when telemetry was never configured."""
    global _tracer_provider, _meter_provider, _span_processor
    with _lock:
        if _tracer_provider is not None:
            try:
                # Cascades into the processor's shutdown (stop writer thread, join, final flush) via
                # the SynchronousMultiSpanProcessor the TracerProvider owns.
                _tracer_provider.shutdown()
            except Exception:
                log.debug("tracer provider shutdown failed", exc_info=True)
        if _meter_provider is not None:
            try:
                _meter_provider.shutdown()
            except Exception:
                log.debug("meter provider shutdown failed", exc_info=True)

            from overlay.otel_metrics import unregister as unregister_metrics

            unregister_metrics()
        _tracer_provider = None
        _meter_provider = None
        _span_processor = None
        set_gauge_provider(None)  # drop the Reader's cache-gauge closure with the providers
        span_gate.set(value=False)
