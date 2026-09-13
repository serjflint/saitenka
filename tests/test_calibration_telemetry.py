"""Late calibration evidence and retry decisions through the native draw path."""

import json

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from test_native_subtitles import Coloring, KnownWords, Scorer, osd_box, reader, settle_jobs

from saitenka.app.otel_export import CTFSpanProcessor
from saitenka.app.telemetry import ActiveGate


def test_missing_bounds_are_retried_and_valid_reply_closes_the_signature(monkeypatch, tmp_path):
    gate = ActiveGate()
    gate.set(value=True)
    path = tmp_path / "trace.json"
    provider = TracerProvider()
    provider.add_span_processor(CTFSpanProcessor(path, gate, start_thread=False))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    session, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    try:
        session.graph.cue.set_subtitle("猫を見る")
        settle_jobs(session, ipc)
        ipc.osd_bounds = osd_box(right=0.0)

        session.graph.subtitle_presentation.draw()
        settle_jobs(session, ipc)
    finally:
        session.close()
        provider.shutdown()

    events = json.loads(path.read_text())["traceEvents"]
    outcomes = [
        event["args"]["outcome"] for event in events if event["name"] == "subtitle_calibration"
    ]
    assert outcomes == ["inconclusive", "agrees"]


def test_missing_bounds_cannot_create_an_unbounded_probe_loop(monkeypatch, tmp_path):
    gate = ActiveGate()
    gate.set(value=True)
    path = tmp_path / "trace.json"
    provider = TracerProvider()
    provider.add_span_processor(CTFSpanProcessor(path, gate, start_thread=False))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    session, ipc, _backend = reader(
        tmp_path, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    try:
        session.graph.cue.set_subtitle("猫を見る")
        settle_jobs(session, ipc)

        for _ in range(10):
            session.graph.subtitle_presentation.draw()
            settle_jobs(session, ipc)
    finally:
        session.close()
        provider.shutdown()

    events = json.loads(path.read_text())["traceEvents"]
    operations = [event for event in events if event["name"] == "subtitle_calibration"]
    assert len(operations) == 2
    assert all(event["args"]["outcome"] == "inconclusive" for event in operations)
    assert any(
        event["name"] == "subtitle_calibration_considered"
        and event["args"]["reason"] == "attempts-exhausted"
        for event in events
    )
