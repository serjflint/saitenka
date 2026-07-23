"""Non-blocking span pipeline: a gated, bounded-queue :class:`SpanProcessor` feeding a Chrome Trace
Format (CTF) :class:`SpanExporter` — no backend, opens directly in ``chrome://tracing`` / Perfetto.

Mirrors mpv's own telemetry model (see ``vibe/observability-plan.md``): the hot path (whatever thread
ends a span) pays a single :class:`~overlay.app.telemetry.ActiveGate` check when the gate is off, and
a non-blocking queue push when it's on — a dedicated writer thread does the actual (slow) export, and
a full queue drops the span and counts it rather than blocking the caller.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import msgspec
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from overlay.app.telemetry import ActiveGate

log = logging.getLogger(__name__)


def _span_to_ctf_event(span: ReadableSpan) -> dict[str, object]:
    """One Chrome Trace Format "complete" (``ph: X``) event. ``ts``/``dur`` are microseconds — CTF's
    unit — converted from the span's nanosecond timestamps."""
    ctx = span.get_span_context()
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    return {
        "name": span.name,
        "cat": "span",
        "ph": "X",
        "ts": start_ns / 1000,
        "dur": max(end_ns - start_ns, 0) / 1000,
        "pid": 1,
        "tid": (ctx.trace_id & 0xFFFFFFFF) if ctx else 0,
        "args": {
            "span_id": format(ctx.span_id, "016x") if ctx else "",
            "trace_id": format(ctx.trace_id, "032x") if ctx else "",
            **dict((span.attributes or {}).items()),
        },
    }


def _counter_event(name: str, value: float, ts_ns: int, pid: int) -> dict[str, object]:
    """A Chrome Trace Format "counter" (``ph: C``) event. Perfetto/``chrome://tracing`` render each
    distinct *name* as its own graph track — this is how a metrics-style value-over-time view shows
    up in the SAME trace.json the spans go into, no separate metrics-visualization stack needed."""
    return {"name": name, "ph": "C", "ts": ts_ns / 1000, "pid": pid, "args": {"value": value}}


class CTFSpanExporter(SpanExporter):
    """Rewrites ``{"traceEvents": [...]}`` to *path* on every export/counter call. Simple (no
    incremental append — a valid CTF file must be one JSON document), acceptable because both spans
    and counters are written off the hot path (the span writer thread / the counter sampler thread).
    ``_lock`` guards ``_events`` + the file write since both threads share this exporter."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._events: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self._lock:
                self._events.extend(_span_to_ctf_event(s) for s in spans)
                self._path.write_bytes(msgspec.json.encode({"traceEvents": self._events}))
        except OSError:
            log.debug("CTF export failed", exc_info=True)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def add_counter_event(self, name: str, value: float, ts_ns: int, pid: int = 1) -> None:
        try:
            with self._lock:
                self._events.append(_counter_event(name, value, ts_ns, pid))
                self._path.write_bytes(msgspec.json.encode({"traceEvents": self._events}))
        except OSError:
            log.debug("CTF counter export failed", exc_info=True)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class GatedSpanProcessor(SpanProcessor):
    """``on_end`` is free when *gate* is off. When on, spans are pushed onto a bounded queue — a
    dedicated writer thread drains it and calls *exporter*. A full queue drops the span and
    increments :attr:`dropped_count` instead of blocking whichever thread just ended a span."""

    def __init__(
        self,
        exporter: SpanExporter,
        gate: ActiveGate,
        maxsize: int = 2048,
        start_thread: bool = True,
    ) -> None:
        """*start_thread=False* skips spawning the writer thread — for tests that need to observe
        queue state (e.g. a full queue) without racing a live consumer; production code always
        leaves it ``True``."""
        super().__init__()
        self._exporter = exporter
        self._gate = gate
        self._queue: queue.Queue[ReadableSpan] = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(target=self._run, name="otel-span-writer", daemon=True)
            self._thread.start()

    @property
    def dropped_count(self) -> int:
        with self._dropped_lock:
            return self._dropped

    def on_end(self, span: ReadableSpan) -> None:
        if not self._gate:
            return
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1

    def _run(self) -> None:  # pragma: no cover — timing-dependent background loop
        while not self._stop.is_set():
            try:
                batch = [self._queue.get(timeout=0.5)]
            except queue.Empty:
                continue
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._exporter.export(batch)

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.force_flush()
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Drain whatever's queued right now, synchronously, on the calling thread."""
        batch: list[ReadableSpan] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._exporter.export(batch)
        return True


class CounterSampler:
    """Periodically calls *sample_fn* (returns a flat ``{name: value}`` snapshot — deliberately a
    plain callable, not coupled to :mod:`overlay.app.otel_metrics` specifically) and writes each
    value as a CTF counter event via :meth:`CTFSpanExporter.add_counter_event`. Same non-blocking,
    local-file philosophy as the span pipeline: one dedicated daemon thread, no push/pull server."""

    def __init__(
        self,
        exporter: CTFSpanExporter,
        sample_fn: Callable[[], dict[str, float]],
        interval: float = 1.0,
    ) -> None:
        self._exporter = exporter
        self._sample_fn = sample_fn
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(interval,), name="otel-counter-sampler", daemon=True
        )
        self._thread.start()

    def _run(self, interval: float) -> None:  # pragma: no cover — timing-dependent background loop
        while not self._stop.wait(interval):
            try:
                values = self._sample_fn()
            except Exception:
                log.debug("counter sample failed", exc_info=True)
                continue
            ts_ns = time.time_ns()
            for name, value in values.items():
                self._exporter.add_counter_event(name, value, ts_ns)

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
