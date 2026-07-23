"""Tests for overlay.app.otel_export: the gated span processor + CTF exporter (Stage 5/6)."""

from __future__ import annotations

import json
import time

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from overlay.app.otel_export import (
    CounterSampler,
    CTFSpanExporter,
    GatedSpanProcessor,
    _span_to_ctf_event,
)
from overlay.app.telemetry import ActiveGate


def _make_span():
    provider = TracerProvider(resource=Resource.create({}))
    mem = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(mem))
    with provider.get_tracer("test").start_as_current_span("op") as span:
        span.set_attribute("k", "v")
    provider.shutdown()
    return mem.get_finished_spans()[0]


def test_ctf_event_shape():
    span = _make_span()
    event = _span_to_ctf_event(span)
    assert event["name"] == "op"
    assert event["ph"] == "X"
    assert event["ts"] >= 0
    assert event["dur"] >= 0
    assert event["args"]["k"] == "v"
    assert len(event["args"]["span_id"]) == 16
    assert len(event["args"]["trace_id"]) == 32


def test_ctf_exporter_writes_valid_json(tmp_path):
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    span = _make_span()
    exporter.export([span])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["traceEvents"][0]["name"] == "op"


def test_gate_off_drops_spans_without_touching_queue(tmp_path):
    exporter = CTFSpanExporter(tmp_path / "trace.json")
    gate = ActiveGate()  # off by default
    processor = GatedSpanProcessor(exporter, gate)
    try:
        processor.on_end(_make_span())
        assert processor._queue.qsize() == 0
        assert processor.dropped_count == 0  # a closed gate isn't a "drop" — it's opted out
    finally:
        processor.shutdown()


def test_gate_on_queues_and_writer_thread_exports(tmp_path):
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    gate = ActiveGate()
    gate.set(True)
    processor = GatedSpanProcessor(exporter, gate)
    try:
        processor.on_end(_make_span())
        for _ in range(50):
            if path.exists() and json.loads(path.read_text(encoding="utf-8"))["traceEvents"]:
                break
            time.sleep(0.05)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["traceEvents"]) == 1
    finally:
        processor.shutdown()


def test_full_queue_increments_dropped_count(tmp_path):
    exporter = CTFSpanExporter(tmp_path / "trace.json")
    gate = ActiveGate()
    gate.set(True)
    # start_thread=False: no live consumer racing to drain the queue between our two puts.
    processor = GatedSpanProcessor(exporter, gate, maxsize=1, start_thread=False)
    try:
        span = _make_span()
        processor._queue.put_nowait(span)
        processor.on_end(span)
        assert processor.dropped_count == 1
    finally:
        processor.shutdown()


def test_force_flush_drains_synchronously(tmp_path):
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    gate = ActiveGate()
    processor = GatedSpanProcessor(exporter, gate, maxsize=8, start_thread=False)
    try:
        span = _make_span()
        processor._queue.put_nowait(span)
        processor.force_flush()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["traceEvents"]) == 1
    finally:
        processor.shutdown()


def test_add_counter_event_writes_ctf_counter_shape(tmp_path):
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    exporter.add_counter_event("prefetch.queue_depth", 3, time.time_ns())
    data = json.loads(path.read_text(encoding="utf-8"))
    (event,) = data["traceEvents"]
    assert event["name"] == "prefetch.queue_depth"
    assert event["ph"] == "C"
    assert event["args"]["value"] == 3


def test_add_counter_event_and_export_interleave_without_corrupting_the_file(tmp_path):
    """Regression guard: spans (writer thread) and counters (sampler thread) share one exporter —
    both mutate _events + write the same file, so this must stay a valid single JSON document."""
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    exporter.export([_make_span()])
    exporter.add_counter_event("gil_enabled", 0, time.time_ns())
    exporter.export([_make_span()])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["traceEvents"]) == 3
    kinds = sorted(e["ph"] for e in data["traceEvents"])
    assert kinds == ["C", "X", "X"]


def test_counter_sampler_writes_periodic_samples(tmp_path):
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    calls = [{"a": 1.0}, {"a": 2.0}]

    def sample_fn():
        return calls.pop(0) if calls else {"a": 2.0}

    sampler = CounterSampler(exporter, sample_fn, interval=0.05)
    try:
        for _ in range(100):
            if path.exists() and len(json.loads(path.read_text())["traceEvents"]) >= 2:
                break
            time.sleep(0.02)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["traceEvents"]) >= 2
        assert all(e["name"] == "a" for e in data["traceEvents"])
    finally:
        sampler.shutdown()


def test_counter_sampler_survives_a_failing_sample_fn(tmp_path):
    """A sample_fn exception must not kill the background thread — it should keep trying on the
    next tick, same as any other 'never let a diagnostic crash the app' path in this codebase."""
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    calls = {"n": 0}

    def sample_fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"ok": 1.0}

    sampler = CounterSampler(exporter, sample_fn, interval=0.05)
    try:
        for _ in range(100):
            if path.exists():
                break
            time.sleep(0.02)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert any(e["name"] == "ok" for e in data["traceEvents"])
    finally:
        sampler.shutdown()
