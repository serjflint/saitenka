"""OpenTelemetry tracer/meter provider lifecycle — fully opt-in, fully no-op when disabled.

See ``vibe/observability-plan.md``. The ``opentelemetry`` package (the ``observability`` extra) is
imported lazily, only inside :func:`configure`, and only once ``TelemetryOptions.enabled`` is true —
a default install pays zero import cost, and a config with telemetry off never touches the SDK at
all: no providers, no threads, no export directory created.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, final

from overlay.app.config import TelemetryOptions
from overlay.app.paths import cache_dir

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    from overlay.app.otel_export import GatedSpanProcessor

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

    def set(self, value: bool) -> None:
        self._on = value


_lock = threading.Lock()
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_span_processor: GatedSpanProcessor | None = None

#: Gates the span pipeline. Starts off (so it costs nothing before/without telemetry); `configure()`
#: turns it on as part of enabling telemetry — see the comment there. Exists as a separate switch
#: from `TelemetryOptions.enabled` for a future dynamic on/off (a doctor/keybind hook toggling
#: capture without a restart), not as a second "is telemetry on" gate a user has to know about.
span_gate = ActiveGate()


def export_dir(options: TelemetryOptions) -> Path:
    return Path(options.export_dir) if options.export_dir else cache_dir() / "telemetry"


def is_enabled() -> bool:
    """True once :func:`configure` has stood up live providers (i.e. telemetry is enabled AND the
    ``observability`` extra is installed)."""
    return _tracer_provider is not None


def dropped_span_count() -> int:
    return _span_processor.dropped_count if _span_processor is not None else 0


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
                "telemetry.enabled=true but the 'observability' extra isn't installed "
                "(pip install 'saitenka-overlay[observability]') — telemetry stays off"
            )
            return

        from overlay.app.otel_export import CTFSpanExporter, GatedSpanProcessor
        from overlay.app.otel_metrics import register as register_metrics

        out_dir = export_dir(options)
        out_dir.mkdir(parents=True, exist_ok=True)

        tp = TracerProvider()
        processor = GatedSpanProcessor(CTFSpanExporter(out_dir / "trace.json"), span_gate)
        tp.add_span_processor(processor)
        # TelemetryOptions.enabled is the actual opt-in switch; the gate defaulting off would mean
        # enabling telemetry produces logs + metrics but NO trace ever, since nothing else flips it —
        # confirmed live via a real `run --demo-word` session before this line was added. The gate
        # stays around for a future dynamic on/off (a doctor/keybind hook toggling capture without a
        # restart), not as a second "are we actually enabled" switch.
        span_gate.set(True)
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
                _tracer_provider.shutdown()
            except Exception:
                log.debug("tracer provider shutdown failed", exc_info=True)
        if _meter_provider is not None:
            try:
                _meter_provider.shutdown()
            except Exception:
                log.debug("meter provider shutdown failed", exc_info=True)

            from overlay.app.otel_metrics import unregister as unregister_metrics

            unregister_metrics()
        _tracer_provider = None
        _meter_provider = None
        _span_processor = None
        span_gate.set(False)
