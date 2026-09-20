"""Metrics stay consistent regardless of which event discovers the missed deadline."""

import pytest
from test_cue_color_timeline import _telemetry
from test_native_subtitles import FakeIPC
from util import record_spans

from saitenka import otel_metrics
from saitenka.app.color_telemetry import ColorTelemetry


@pytest.mark.parametrize("trigger", ["timer", "qualification", "acknowledgment"])
def test_each_deadline_discovery_path_counts_exactly_once(trigger):
    now = [0.0]
    ipc = FakeIPC()
    with _telemetry():
        telemetry = ColorTelemetry(ipc, clock=lambda: now[0])
        telemetry.begin("navigation")
        telemetry.bind((1000, "target"))
        telemetry.qualify(
            1, frozenset({0}), frozenset() if trigger == "qualification" else frozenset({0})
        )
        write = telemetry.submit(1, "overpaint", frozenset({0}))
        now[0] = 0.020

        if trigger == "timer":
            assert ipc.fire_runtime_timer("subtitle:color-deadline")
        elif trigger == "qualification":
            telemetry.qualify(1, frozenset({0}), frozenset({0}))
        else:
            telemetry.settle(write, accepted=True)
        telemetry.retire("shutdown")
        snapshot = otel_metrics.snapshot()

    assert snapshot["saitenka.subtitle.color_deadline_misses"]["value"] == 1
    assert snapshot["saitenka.subtitle.color_outcomes"]["value"] == 1
    assert snapshot["saitenka.subtitle.color_pending"]["value"] == 0


def test_navigation_replaced_before_target_binding_stays_in_attempt_denominator():
    with _telemetry():
        ipc = FakeIPC()
        telemetry = ColorTelemetry(ipc, clock=lambda: 0.0)
        telemetry.begin("navigation")

        telemetry.begin("navigation")
        telemetry.retire("shutdown")
        snapshot = otel_metrics.snapshot()

    assert snapshot["saitenka.subtitle.color_outcomes"]["value"] == 2
    assert snapshot["saitenka.subtitle.color_pending"]["value"] == 0
    assert not ipc.fire_runtime_timer("subtitle:color-deadline")


def test_disabled_telemetry_does_not_schedule_a_deadline():
    ipc = FakeIPC()
    telemetry = ColorTelemetry(ipc, clock=lambda: 0.0)

    telemetry.begin("navigation")
    telemetry.bind((1000, "target"))

    assert telemetry.current is None
    assert not ipc.fire_runtime_timer("subtitle:color-deadline")


@pytest.mark.parametrize("resolved", [(2, "resolved"), (2, "provisional")])
@pytest.mark.parametrize("already_acknowledged", [False, True])
def test_corrected_navigation_target_keeps_start_and_rejects_provisional_write(
    resolved, already_acknowledged
):
    now = [0.0]
    with _telemetry():
        telemetry = ColorTelemetry(FakeIPC(), clock=lambda: now[0])
        telemetry.begin("navigation")
        telemetry.bind((1, "provisional"))
        telemetry.qualify(1, frozenset({0}), frozenset({0}))
        old = telemetry.submit(1, "overpaint", frozenset({0}))
        if already_acknowledged:
            now[0] = 0.005
            telemetry.settle(old, accepted=True)
        now[0] = 0.010

        telemetry.bind(resolved, reconcile=True)
        telemetry.qualify(1, frozenset({0}), frozenset({0}))
        telemetry.settle(old, accepted=True)
        new = telemetry.submit(1, "overpaint", frozenset({0}))
        now[0] = 0.025
        telemetry.settle(new, accepted=True)
        telemetry.retire("shutdown")
        metrics = otel_metrics.snapshot()

    assert metrics["saitenka.subtitle.color_outcomes"]["value"] == 1
    assert metrics["saitenka.subtitle.color_ack_ms"]["max"] == pytest.approx(25)
    assert (
        metrics["saitenka.subtitle.color_ack_ms"]["by"]["endpoint=first,kind=navigation"]["count"]
        == 1
    )


def test_repeated_target_has_readable_timestamp_and_distinct_occurrences(monkeypatch):
    spans = record_spans(monkeypatch)
    with _telemetry():
        telemetry = ColorTelemetry(FakeIPC(), clock=lambda: 0.0)
        key = (1, "media", 2, "primary", 16120, "text-digest")
        telemetry.begin("navigation")
        telemetry.bind(key)

        telemetry.begin("navigation")
        telemetry.bind(key)
        telemetry.retire("shutdown")

    targets = [span["attrs"] for span in spans if span["name"] == "subtitle_color_target"]
    assert [target["occurrence"] for target in targets] == [1, 2]
    assert all(target["cue_start_ms"] == 16120 for target in targets)
    assert all(target["text_hash"] == "text-digest" for target in targets)


@pytest.mark.usefixtures("enabled_telemetry")
def test_color_outcome_survives_real_trace_export(monkeypatch, caplog):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from saitenka.app.otel_export import _span_to_ctf_event

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    from telemetry_helpers import enable_span_gate

    enable_span_gate(monkeypatch)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    try:
        with _telemetry():
            telemetry = ColorTelemetry(FakeIPC(), clock=lambda: 0.0)
            telemetry.begin("navigation")
            telemetry.retire("shutdown")
        event = next(
            _span_to_ctf_event(span)
            for span in exporter.get_finished_spans()
            if span.name == "subtitle_color_outcome"
        )
    finally:
        provider.shutdown()

    assert event["args"]["color_status"] == "unknown"
    assert event["args"]["status"] == "unset"
    assert "first_ms" not in event["args"]
    assert "Invalid type NoneType" not in caplog.text


@pytest.mark.usefixtures("enabled_telemetry")
def test_stale_ack_is_reported_without_attributing_it_to_replacement():
    from saitenka.app.telemetry import diagnostic_snapshot

    telemetry = ColorTelemetry(FakeIPC(), clock=lambda: 0.0, configuration_owner=1)
    telemetry.bind((1000, "a" * 32))
    old = telemetry.submit(1, "overprint", frozenset({0}))
    telemetry.bind((2000, "b" * 32))
    telemetry.settle(old, accepted=True)
    history = diagnostic_snapshot("runtime_configuration")["owners"][0]["whole_cue"]["history"]
    stale = next(row for row in history if row["event"] == "subtitle_color_stale_ack")
    assert stale["occurrence"] == 1
    assert stale["device"] == "overprint"
    assert stale["accepted"] is False
    assert telemetry.current.snapshot(0).acknowledged == 0
    telemetry.retire("shutdown")


@pytest.mark.usefixtures("enabled_telemetry")
def test_report_separates_cue_to_submit_from_ack_wait(monkeypatch):
    now = [0.0]
    rows = record_spans(monkeypatch)
    telemetry = ColorTelemetry(FakeIPC(), clock=lambda: now[0])
    telemetry.bind((1000, "a" * 32))
    telemetry.qualify(1, frozenset({0}), frozenset({0}))
    now[0] = 0.003
    write = telemetry.submit(1, "overprint", frozenset({0}))
    now[0] = 0.007
    telemetry.settle(write, accepted=True)
    times = {
        row["name"]: row["attrs"]["elapsed_ms"] for row in rows if "elapsed_ms" in row["attrs"]
    }
    assert times["subtitle_color_submit"] == pytest.approx(3)
    assert times["subtitle_color_ack"] == pytest.approx(7)
    telemetry.retire("shutdown")
