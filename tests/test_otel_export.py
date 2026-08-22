"""Tests for saitenka.app.otel_export: the single gated CTF span/counter processor.

Deterministic tests construct with ``start_thread=False`` and drive writes via ``force_flush()``; the
few that need the live writer thread start it and poll (no fixed sleeps)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from util import await_ready, validate_ctf_document

from saitenka.app.otel_export import CTFSpanProcessor, _span_to_ctf_event
from saitenka.app.telemetry import ActiveGate

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


def _on_gate(**kwargs) -> tuple[CTFSpanProcessor, ActiveGate]:
    """A processor with the gate already on and no writer thread — the deterministic test setup."""
    gate = ActiveGate()
    gate.set(value=True)
    return CTFSpanProcessor(kwargs.pop("path"), gate, start_thread=False, **kwargs), gate


# --- CTF event shape (pure functions) -------------------------------------------------------


def test_ctf_event_shape():
    event = _span_to_ctf_event(_make_span())
    assert event["name"] == "op"
    assert event["ph"] == "X"
    assert event["ts"] >= 0
    assert event["dur"] >= 0
    assert event["args"]["k"] == "v"
    assert len(event["args"]["span_id"]) == 16
    # A root span links to nothing, so it carries no edge and no trace id. `trace_id` is gone
    # entirely: `parent_id` names the edge, and 34 bytes of "same root" was 10.6% of a real file.
    assert "parent_id" not in event["args"]
    assert "trace_id" not in event["args"]


def test_ctf_event_tid_comes_from_thread_id_attribute_not_trace_id():
    """Regression: found by opening a real trace in Perfetto — every independently-started span (no
    parent-child relationship) has a different random trace_id, and the original code used a slice
    of THAT for tid, scattering unrelated spans across a different synthetic "Thread NNNN" track
    each instead of grouping same-thread spans onto one track."""
    event = _span_to_ctf_event(_make_span(**{"thread.id": 424242}))
    assert event["tid"] == 424242
    assert "thread.id" not in event["args"]  # consumed for tid, not duplicated


def test_ctf_event_tid_defaults_to_zero_without_the_attribute():
    assert _span_to_ctf_event(_make_span())["tid"] == 0


def test_two_independent_spans_on_the_same_thread_share_tid():
    """The actual fix, exercised the way production code hits it: otel_metrics.traced() stamps the
    real native thread id, so two spans from the same thread (even with unrelated trace_ids, since
    neither is a child of the other) land on the same CTF track."""
    same = 999
    a, b = _make_span(**{"thread.id": same}), _make_span(**{"thread.id": same})
    assert a.context.trace_id != b.context.trace_id  # genuinely independent
    assert _span_to_ctf_event(a)["tid"] == _span_to_ctf_event(b)["tid"] == same


# --- on_end / gate / drop --------------------------------------------------------------------


def test_gate_off_drops_spans_without_touching_queue(tmp_path):
    proc = CTFSpanProcessor(tmp_path / "trace.json", ActiveGate(), start_thread=False)  # gate off
    proc.on_end(_make_span())
    assert proc._queue.qsize() == 0
    assert proc.dropped_count == 0  # a closed gate isn't a "drop" — it's opted out


def test_gate_on_queues_the_span(tmp_path):
    proc, _ = _on_gate(path=tmp_path / "trace.json")
    proc.on_end(_make_span())
    assert proc._queue.qsize() == 1


def test_full_queue_increments_dropped_count(tmp_path):
    proc, _ = _on_gate(path=tmp_path / "trace.json", maxsize=1)
    proc.on_end(_make_span())  # fills the single slot
    proc.on_end(_make_span())  # no room → dropped, not blocked
    assert proc._queue.qsize() == 1
    assert proc.dropped_count == 1


# --- force_flush / file output ---------------------------------------------------------------


def test_force_flush_writes_valid_ctf(tmp_path):
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path)
    proc.on_end(_make_span())
    proc.force_flush()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["traceEvents"][0]["name"] == "op"


def test_force_flush_with_nothing_queued_writes_no_file(tmp_path):
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path)
    proc.force_flush()
    assert not path.exists()  # empty batch → no write, no empty/invalid document


def test_write_recreates_a_vanished_trace_file(tmp_path):
    """A cache cleanup / session rotation can delete the trace file mid-run. The writer must recreate it
    and keep the batch, not retry the dead ``r+b`` append every tick (the ~579-per-run 'CTF write
    failed' spam)."""
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path)
    proc.on_end(_make_span())
    proc.force_flush()
    assert path.exists()

    path.unlink()  # the file vanishes out from under the writer
    proc.on_end(_make_span())
    proc.force_flush()  # must recreate, not raise or give up

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["traceEvents"]) == 1  # fresh valid document holding the post-vanish batch


def test_write_recreates_a_vanished_trace_directory(tmp_path):
    """Even the parent dir can be cleaned; recreation must mkdir it back, not fail."""
    path = tmp_path / "sub" / "trace.json"
    proc, _ = _on_gate(path=path)
    proc.on_end(_make_span())
    proc.force_flush()

    for p in (path, path.parent):
        p.unlink() if p.is_file() else p.rmdir()
    proc.on_end(_make_span())
    proc.force_flush()

    assert json.loads(path.read_text(encoding="utf-8"))["traceEvents"]  # recreated dir + file


def test_repeated_recoverable_vanish_never_logs(tmp_path, caplog):
    """The #245 regression was NOISE, not loss: a vanished trace file made the writer log 'CTF write
    failed' on *every* tick (~579×/run). Recovery is now silent — a single-vanish is always recovered
    by the recreate-and-retry, so a long run of vanish→flush cycles must emit ZERO failure lines while
    still ending in a valid document. Guards the log-count, which the recovery tests above don't."""
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path)
    with caplog.at_level(logging.DEBUG, logger="saitenka.app.otel_export"):
        for _ in range(50):
            path.unlink(missing_ok=True)  # cache cleanup deletes it out from under the writer
            proc.on_end(_make_span())
            proc.force_flush()  # recreate + write, silently
    assert "CTF write failed" not in caplog.text  # recovery is silent — no per-tick spam
    assert json.loads(path.read_text(encoding="utf-8"))["traceEvents"]  # last batch still landed


def test_genuinely_unwritable_target_logs_once_per_flush_not_per_tick(tmp_path, caplog):
    """A target that CAN'T be recovered (its parent is a file, so mkdir keeps failing) is the only case
    that logs — and at most ONCE per flush, never an inner-retry storm. K flushes → exactly K lines, so
    the guard distinguishes 'genuinely broken' from the recoverable vanish above (which logs zero)."""
    parent_is_a_file = tmp_path / "blocker"
    parent_is_a_file.write_text("not a directory", encoding="utf-8")
    proc, _ = _on_gate(path=parent_is_a_file / "trace.json")
    with caplog.at_level(logging.DEBUG, logger="saitenka.app.otel_export"):
        for _ in range(5):
            proc.on_end(_make_span())
            proc.force_flush()  # never raises — the writer swallows the OSError
    failures = [r for r in caplog.records if "CTF write failed" in r.message]
    assert len(failures) == 5  # one per failed batch, not the ~579-per-run storm


def test_second_flush_appends_without_rewriting_prior_events(tmp_path):
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path)
    proc.on_end(_make_span())
    proc.force_flush()
    proc.on_end(_make_span())
    proc.force_flush()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["traceEvents"]) == 2  # both present, document still valid


# --- counter sampling (folded into the processor via sample_fn) ------------------------------


def test_sample_fn_writes_counter_tracks(tmp_path):
    path = tmp_path / "trace.json"
    # interval=0 → every flush samples; start_thread=False → deterministic.
    proc, _ = _on_gate(path=path, sample_fn=lambda: {"a": 1.0, "b": 2.0}, interval=0.0)
    proc.force_flush()
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data["traceEvents"]
    assert {e["name"] for e in events} == {"a", "b"}
    assert all(e["ph"] == "C" for e in events)
    assert {e["args"]["value"] for e in events} == {1.0, 2.0}


def test_spans_and_counters_interleave_into_one_valid_document(tmp_path):
    path = tmp_path / "trace.json"
    values = iter([{"gil_enabled": 0.0}, {"gil_enabled": 1.0}])
    proc, _ = _on_gate(path=path, sample_fn=lambda: next(values), interval=0.0)
    proc.on_end(_make_span())
    proc.force_flush()
    proc.on_end(_make_span())
    proc.force_flush()
    data = json.loads(path.read_text(encoding="utf-8"))
    kinds = sorted(e["ph"] for e in data["traceEvents"])
    assert kinds == ["C", "C", "X", "X"]  # 2 spans, 2 counter samples, one valid document


def test_an_unchanged_counter_is_not_re_emitted(tmp_path):
    """Perfetto holds a counter track's last value until the next point, so a series that did not
    move draws the identical line for its bytes. 55 of 140 series in a real session never moved at
    all — several of them one-shot startup measurements resampled every second for the whole run."""
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path, sample_fn=lambda: {"steady": 7.0}, interval=0.0)
    proc.force_flush()
    proc.force_flush()
    proc.force_flush()

    points = [
        e for e in json.loads(path.read_text(encoding="utf-8"))["traceEvents"] if e["ph"] == "C"
    ]

    assert [e["args"]["value"] for e in points] == [7.0]


def test_a_counter_that_moves_is_re_emitted(tmp_path):
    """Negative control for the test above — the dedup must key on the VALUE, not silence the series
    after its first point."""
    path = tmp_path / "trace.json"
    values = iter([{"n": 1.0}, {"n": 1.0}, {"n": 2.0}])
    proc, _ = _on_gate(path=path, sample_fn=lambda: next(values), interval=0.0)
    proc.force_flush()
    proc.force_flush()
    proc.force_flush()

    points = [
        e for e in json.loads(path.read_text(encoding="utf-8"))["traceEvents"] if e["ph"] == "C"
    ]

    assert [e["args"]["value"] for e in points] == [1.0, 2.0]


def test_sampling_respects_interval(tmp_path):
    """With a long interval, a flush right after construction does NOT re-sample (the constructor
    stamps _last_sample), so only spans are written."""
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path, sample_fn=lambda: {"a": 1.0}, interval=3600.0)
    proc.on_end(_make_span())
    proc.force_flush()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert all(e["ph"] == "X" for e in data["traceEvents"])  # no counter yet


def test_writer_survives_a_failing_sample_fn(tmp_path):
    """A sample_fn exception must not lose the span or crash the flush — 'never let a diagnostic
    crash the app' path."""
    path = tmp_path / "trace.json"

    def boom() -> dict[str, float]:
        raise RuntimeError("boom")

    proc, _ = _on_gate(path=path, sample_fn=boom, interval=0.0)
    proc.on_end(_make_span())
    proc.force_flush()  # must not raise
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [e["name"] for e in data["traceEvents"]] == ["op"]


# --- live writer thread ----------------------------------------------------------------------


def _settled_events(path) -> list[dict]:
    """Events on disk, or `[]` while the writer is mid-rewrite.

    The writer thread rewrites the whole document each flush, so a reader that lands between the
    truncate and the last byte parses a fragment. Half a document is not "no events yet" to
    `json`, it is `JSONDecodeError` — raised out of the *predicate*, which no deadline can catch
    and which names a different test on every run. Treating a torn read as not-yet-ready is what
    makes the wait a wait rather than a race the reader usually wins.
    """
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))["traceEvents"]
    except (json.JSONDecodeError, KeyError):
        return []


def test_live_writer_thread_exports_a_span(tmp_path):
    path = tmp_path / "trace.json"
    gate = ActiveGate()
    gate.set(value=True)
    proc = CTFSpanProcessor(path, gate, interval=0.05)
    try:
        proc.on_end(_make_span())
        await_ready(lambda: bool(_settled_events(path)), "the writer thread never flushed the span")
    finally:
        proc.shutdown()
    assert len(_settled_events(path)) == 1  # after shutdown: no writer left to tear the read


def test_live_writer_thread_samples_counters_periodically(tmp_path):
    path = tmp_path / "trace.json"
    gate = ActiveGate()
    gate.set(value=True)
    proc = CTFSpanProcessor(path, gate, sample_fn=lambda: {"a": 1.0}, interval=0.05)
    try:
        # A deadline, not `for _ in range(100)`: that is a scheduling budget in a timeout's clothes,
        # and under `test-ft` the writer thread loses it before it is ever scheduled.
        await_ready(
            lambda: any(e["ph"] == "C" for e in _settled_events(path)),
            "the writer thread never sampled a counter",
        )
    finally:
        proc.shutdown()
    counters = [e for e in _settled_events(path) if e["ph"] == "C"]
    assert counters and all(e["name"] == "a" for e in counters)


def test_shutdown_flushes_the_tail(tmp_path):
    path = tmp_path / "trace.json"
    gate = ActiveGate()
    gate.set(value=True)
    proc = CTFSpanProcessor(path, gate, interval=5.0)  # long interval: rely on shutdown to flush
    proc.on_end(_make_span())
    proc.shutdown()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["traceEvents"]) == 1


# --- CTF schema conformance ------------------------------------------------------------------


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
    """The actual production shape (spans + a counter sample through one processor) must produce a
    conformant document — catches a future change to the writer breaking the format even if nobody
    remembers to regenerate the fixture."""
    path = tmp_path / "trace.json"
    proc, _ = _on_gate(path=path, sample_fn=lambda: {"gil_enabled": 0.0}, interval=0.0)
    proc.on_end(_make_span())
    proc.force_flush()
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
