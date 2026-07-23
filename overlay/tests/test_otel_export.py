"""Tests for overlay.app.otel_export: the gated span processor + CTF exporter (Stage 5/6)."""

from __future__ import annotations

import json
import time

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from overlay.app.otel_export import CTFSpanExporter, GatedSpanProcessor, _span_to_ctf_event
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
    processor = GatedSpanProcessor(exporter, gate, maxsize=1)
    try:
        # fill the queue directly so the writer thread can't drain it before we overflow it
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
    processor = GatedSpanProcessor(exporter, gate, maxsize=8)
    try:
        span = _make_span()
        processor._queue.put_nowait(span)
        processor.force_flush()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["traceEvents"]) == 1
    finally:
        processor.shutdown()
