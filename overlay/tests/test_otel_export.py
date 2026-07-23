"""Tests for overlay.app.otel_export: the gated span processor + CTF exporter (Stage 5/6)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
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
from util import validate_ctf_document

GOLDEN_TRACE = Path(__file__).resolve().parent / "golden" / "sample_trace.json"


def _make_span(**extra_attrs):
    provider = TracerProvider(resource=Resource.create({}))
    mem = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(mem))
    with provider.get_tracer("test").start_as_current_span("op") as span:
        span.set_attribute("k", "v")
        for k, v in extra_attrs.items():
            span.set_attribute(k, v)
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


def test_ctf_event_tid_comes_from_thread_id_attribute_not_trace_id():
    """Regression: found by opening a real trace in Perfetto — every independently-started span (no
    parent-child relationship) has a different random trace_id, and the original code used a slice
    of THAT for tid, scattering unrelated spans across a different synthetic "Thread NNNN" track
    each instead of grouping same-thread spans onto one track."""
    span = _make_span(**{"thread.id": 424242})
    event = _span_to_ctf_event(span)
    assert event["tid"] == 424242
    assert "thread.id" not in event["args"]  # consumed for tid, not duplicated


def test_ctf_event_tid_defaults_to_zero_without_the_attribute():
    span = _make_span()
    assert _span_to_ctf_event(span)["tid"] == 0


def test_two_independent_spans_on_the_same_thread_share_tid():
    """The actual fix, exercised the way production code hits it: otel_metrics.traced() stamps the
    real native thread id, so two spans from the same thread (even with unrelated trace_ids, since
    neither is a child of the other) land on the same CTF track."""
    same_thread_id = 999
    a = _make_span(**{"thread.id": same_thread_id})
    b = _make_span(**{"thread.id": same_thread_id})
    assert a.context.trace_id != b.context.trace_id  # confirms they're genuinely independent
    ea, eb = _span_to_ctf_event(a), _span_to_ctf_event(b)
    assert ea["tid"] == eb["tid"] == same_thread_id


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


# --- CTF schema conformance -----------------------------------------------------------------


def test_sample_trace_fixture_is_valid_ctf():
    """tests/golden/sample_trace.json is a REAL trace captured from a live session (real mpv, real
    mouse-driven hovers, telemetry enabled) — not a byte-for-byte golden (ts/span_id/trace_id/tid
    are inherently non-deterministic), just a checked-in reference example, validated for shape.
    Regenerate by running a live session with telemetry enabled if the export format changes on
    purpose."""
    data = json.loads(GOLDEN_TRACE.read_text(encoding="utf-8"))
    validate_ctf_document(data)
    names = {e["name"] for e in data["traceEvents"] if e["ph"] == "X"}
    assert {"render", "upload", "hit_test"} <= names
    counter_names = {e["name"] for e in data["traceEvents"] if e["ph"] == "C"}
    assert "runtime.gil_enabled" in counter_names


def test_live_pipeline_output_is_valid_ctf(tmp_path):
    """The actual production pipeline (GatedSpanProcessor + CTFSpanExporter + a counter event),
    not just the fixture, must produce a conformant document — catches a future change to either
    exporter breaking the format even if nobody remembers to regenerate the fixture."""
    path = tmp_path / "trace.json"
    exporter = CTFSpanExporter(path)
    gate = ActiveGate()
    gate.set(True)
    processor = GatedSpanProcessor(exporter, gate, start_thread=False)
    processor.on_end(_make_span())
    processor.force_flush()
    exporter.add_counter_event("gil_enabled", 0, time.time_ns())
    processor.shutdown()

    validate_ctf_document(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "broken",
    [
        {},  # missing traceEvents entirely
        {"traceEvents": "not-a-list"},
        {"traceEvents": [{"ph": "X"}]},  # missing name/ts/pid
        {"traceEvents": [{"name": "a", "ph": "X", "ts": 0, "pid": 1}]},  # X missing dur/tid
        {
            "traceEvents": [{"name": "a", "ph": "C", "ts": 0, "pid": 1, "args": {}}]
        },  # C missing value
        {"traceEvents": [{"name": "a", "ph": "?", "ts": 0, "pid": 1}]},  # unsupported ph
    ],
)
def test_validate_ctf_document_rejects_malformed_input(broken):
    """The validator must actually catch problems, not rubber-stamp anything with a traceEvents key —
    proven by feeding it documents shaped like real regressions (a dropped required field per ph
    type, an unsupported ph, a non-list traceEvents)."""
    with pytest.raises(AssertionError):
        validate_ctf_document(broken)
