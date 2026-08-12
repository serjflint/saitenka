"""Non-blocking CTF trace pipeline: one gated :class:`SpanProcessor` that owns one bounded queue and
one writer thread — the sole toucher of the trace file. No backend, opens directly in
``chrome://tracing`` / Perfetto.

Collapsed on purpose (see ``vibe/observability-pipeline-redesign.md``): the OTel processor/exporter
split earns its keep for swappable protocol backends, not a local file we'll never swap, and stock
`BatchSpanProcessor` buffers in a `collections.deque` whose free-threading safety this repo declines to
bet on. So this is a single custom processor — the `SimpleSpanProcessor` pattern (export logic inline)
extended with a batching queue.

Mirrors mpv's telemetry model: the thread that ends a span pays only a gate check + a non-blocking
`put_nowait`; encoding and file I/O happen entirely on the writer thread. Counters aren't queued at
all — the writer self-samples *sample_fn* on an interval and writes them as CTF counter tracks in the
same file. A full queue drops the span and counts it rather than blocking the caller.

Deadlock guardrail (``find-lock-bugs`` Pattern 1): ``on_end`` briefly holds `queue.Queue`'s
non-reentrant mutex — never start/end a span from a ``__del__``/finalizer, or GC firing mid-`put_nowait`
could re-enter it.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

import msgspec
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from saitenka.app.telemetry import ActiveGate

log = logging.getLogger(__name__)


def _span_to_ctf_event(span: ReadableSpan) -> dict[str, object]:
    """One Chrome Trace Format "complete" (``ph: X``) event. ``ts``/``dur`` are microseconds — CTF's
    unit — converted from the span's nanosecond timestamps. ``tid`` comes from the ``thread.id``
    attribute ``otel_metrics.traced()`` stamps on every span — NOT the trace id: two independently
    -started spans (no parent-child relationship) get different random trace ids, and using that for
    tid scatters unrelated spans across a different synthetic "thread" track each in Perfetto,
    instead of grouping same-thread spans onto one track the way a timeline view is meant to read."""
    ctx = span.get_span_context()
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    attrs = dict((span.attributes or {}).items())
    tid = attrs.pop("thread.id", 0)
    return {
        "name": span.name,
        "cat": "span",
        "ph": "X",
        "ts": start_ns / 1000,
        "dur": max(end_ns - start_ns, 0) / 1000,
        "pid": 1,
        "tid": tid,
        "args": {
            "span_id": format(ctx.span_id, "016x") if ctx else "",
            "trace_id": format(ctx.trace_id, "032x") if ctx else "",
            **attrs,
        },
    }


def _counter_event(name: str, value: float, ts_ns: int, pid: int = 1) -> dict[str, object]:
    """A Chrome Trace Format "counter" (``ph: C``) event. Perfetto/``chrome://tracing`` render each
    distinct *name* as its own graph track — this is how a metrics-style value-over-time view shows
    up in the SAME trace.json the spans go into, no separate metrics-visualization stack needed."""
    return {"name": name, "ph": "C", "ts": ts_ns / 1000, "pid": pid, "args": {"value": value}}


class CTFSpanProcessor(SpanProcessor):
    """The single stage of the CTF pipeline and the only thing that ever touches *path*.

    ``on_end`` (hot path) is gate + one bounded ``put_nowait`` — a full queue drops the span and bumps
    :attr:`dropped_count` instead of blocking. One writer thread drains the queue, encodes off the
    ending thread, self-samples *sample_fn* every *interval* seconds into CTF counter tracks, and does
    exactly one ``open`` + (``seek`` +) one ``write`` per batch. ``_io_lock`` wraps drain **and** write
    as a single critical section so a concurrent :meth:`force_flush` can't split the queue or return
    before a write lands; the separate ``_dropped_lock`` keeps ``on_end``'s drop bump off the I/O path.
    """

    _CLOSING = b"]}"

    def __init__(
        self,
        path: Path,
        gate: ActiveGate,
        *,
        sample_fn: Callable[[], dict[str, float]] | None = None,
        interval: float = 1.0,
        maxsize: int = 2048,
        start_thread: bool = True,
    ) -> None:
        """*sample_fn* is polled by the writer thread every *interval* seconds (``None`` = no
        counters). *start_thread=False* skips the writer thread — tests drive flushes deterministically
        via :meth:`force_flush`; with ``interval=0`` every flush also samples. Production leaves both
        defaults."""
        super().__init__()
        self._path = path
        self._gate = gate
        self._sample_fn = sample_fn
        self._interval = interval
        self._queue: queue.Queue[ReadableSpan] = queue.Queue(maxsize=maxsize)
        self._io_lock = threading.Lock()
        self._dropped_lock = threading.Lock()
        self._dropped = 0
        self._initialized = False
        self._last_sample = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(target=self._run, name="ctf-writer", daemon=True)
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
                seed = [self._queue.get(timeout=self._interval)]
            except queue.Empty:
                seed = []
            self._flush(seed)

    def _flush(self, seed: list[ReadableSpan]) -> None:
        """Drain the queue into *seed*, encode, sample counters if the interval elapsed, and write —
        all under ``_io_lock`` so this whole unit is atomic against a concurrent flusher."""
        with self._io_lock:
            spans = seed
            while True:
                try:
                    spans.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            events = [_span_to_ctf_event(s) for s in spans]
            if self._sample_fn is not None:
                now = time.monotonic()
                if now - self._last_sample >= self._interval:
                    self._last_sample = now
                    events.extend(self._sample_counter_events())
            if events:
                self._write_events(events)

    def _sample_counter_events(self) -> list[dict[str, object]]:
        try:
            values = self._sample_fn() if self._sample_fn is not None else {}
        except Exception:
            log.debug("counter sample failed", exc_info=True)
            return []
        ts_ns = time.time_ns()
        return [_counter_event(name, value, ts_ns) for name, value in values.items()]

    def _write_events(self, events: list[dict[str, object]]) -> None:
        """One open + one write for the whole batch. First call (or after the file vanishes) creates the
        document; later calls seek past the trailing ``]}`` and splice the batch in — O(new events), not
        O(events so far). Caller holds ``_io_lock`` (guards ``_initialized`` + the file position)."""
        chunk = b",".join(msgspec.json.encode(e) for e in events)
        try:
            self._splice(chunk)
        except OSError:
            # The trace file (or its dir) vanished mid-run — a cache cleanup or a session rotation
            # removed it out from under us. Recreate it and re-write THIS batch, instead of retrying the
            # dead ``r+b`` append every tick forever (seen ~579×/run at debug). Only a second, immediate
            # failure is logged — a genuinely unwritable target, not a transient disappearance.
            self._initialized = False
            try:
                self._splice(chunk)
            except OSError:
                log.debug("CTF write failed", exc_info=True)

    def _splice(self, chunk: bytes) -> None:
        if not self._initialized:
            self._path.parent.mkdir(parents=True, exist_ok=True)  # dir may have been cleaned too
            with self._path.open("wb") as f:
                f.write(b'{"traceEvents":[' + chunk + self._CLOSING)
            self._initialized = True
        else:
            with self._path.open("r+b") as f:
                f.seek(-len(self._CLOSING), 2)
                f.write(b"," + chunk + self._CLOSING)

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002  # base-class signature; unused
        """Drain + write synchronously on the calling thread. Airtight only against a stopped writer
        (:meth:`shutdown` joins first); while the writer runs it may hold a just-``get``'d span outside
        the lock, so a racing force_flush can write later spans first — a harmless ts reorder Perfetto
        sorts out, never a loss. Unreachable in production (force_flush only runs post-join at shutdown)
        and in tests (``start_thread=False``)."""
        self._flush([])
        return True

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._flush([])
