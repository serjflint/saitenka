"""Submission context and acknowledged latency survive the actual report pipeline."""

import json
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from PIL import Image
from test_lifecycle_surfaces import _DeferredIPC
from test_report import _hermetic

from saitenka import otel_metrics
from saitenka.app import lifecycle_surfaces, report
from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
from saitenka.app.otel_export import CTFSpanProcessor
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.telemetry import ActiveGate
from saitenka.app.trace_report import load_startup_trace, startup_json
from saitenka.mpvio.osd import Overlay


@pytest.mark.timeout(5)
def test_observed_cue_carries_revision_through_geometry_and_color_export(monkeypatch, tmp_path):
    from test_native_subtitles import Coloring, FakeIPC, KnownWords, Scorer, reader, settle_jobs

    from saitenka.runtime import SessionMailbox
    from saitenka.runtime.correlator import EffectCorrelator
    from saitenka.runtime.jobs import JobBroker

    mailbox = SessionMailbox()
    broker = JobBroker(mailbox)
    correlator = EffectCorrelator(mailbox, SimpleNamespace(connection_epoch=0), job_adapter=broker)

    def register(_ipc, name, policy, handler):
        if name != "subtitle-geometry":
            return False
        broker.register(name, policy, handler)
        return True

    def deliver(_ipc):
        envelope = mailbox.receive(timeout=2)
        assert envelope is not None
        correlator.handle_terminal(envelope.payload)

    monkeypatch.setattr(FakeIPC, "register_runtime_job_lane", register)
    monkeypatch.setattr(
        FakeIPC, "submit_runtime_job", lambda _ipc, **kwargs: correlator.submit_job(**kwargs)
    )
    monkeypatch.setattr(FakeIPC, "deliver_runtime_jobs", deliver)
    monkeypatch.setattr(
        FakeIPC,
        "close_runtime_job_lane",
        lambda _ipc, name, timeout=2: broker.close_lane(name, timeout),
    )

    config = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    config.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{directory}"\n')
    gate = ActiveGate()
    gate.set(value=True)
    provider = TracerProvider()
    provider.add_span_processor(
        CTFSpanProcessor(directory / "trace-1.json", gate, start_thread=False)
    )
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    session, ipc, _backend = reader(
        tmp_path,
        correlated_surfaces=True,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"]))),
    )
    try:
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)
    finally:
        session.close()
        broker.close()
        mailbox.close()
        provider.shutdown()
    archive = report.build_report_bundle(
        tmp_path / "reports", timestamp="cue", diagnostic_detail=True
    )
    events = load_startup_trace(archive)
    cue = next(event for event in events if event.get("name") == "cue_reconcile")
    revision = cue["args"]["cue_revision"]
    geometry = [event for event in events if event.get("name") == "subtitle_geometry_render"]
    colors = [
        event
        for event in events
        if event.get("name") == "surface_write"
        and event["args"].get("slot") == "subtitle-native-focus"
        and event["args"].get("events")
    ]
    assert geometry and colors
    assert all(event["args"].get("cue_revision") == revision for event in [*geometry, *colors])
    assert all(event["args"].get("outcome") == "succeeded" for event in colors)


def test_reader_reports_acknowledgment_wait_and_preserves_originating_cue(monkeypatch, tmp_path):
    config = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    config.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{directory}"\n')
    gate = ActiveGate()
    gate.set(value=True)
    provider = TracerProvider()
    provider.add_span_processor(
        CTFSpanProcessor(directory / "trace-1.json", gate, start_thread=False)
    )
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    clock = iter((100.0, 100.25))
    monkeypatch.setattr(
        lifecycle_surfaces, "time", SimpleNamespace(perf_counter=lambda: next(clock))
    )
    ipc = _DeferredIPC()
    surfaces = LifecycleSurfaces(Overlay(ipc, runtime_submit=ipc.submit_runtime_mpv))
    with otel_metrics.traced("cue_reconcile", cue="text-free-digest", cue_revision="42"):
        surfaces.present(Image.new("RGBA", (2, 2), "white"), 0, 0, oid=OverlayId.TOAST)

    ipc.finish(0)
    provider.shutdown()
    archive = report.build_report_bundle(
        tmp_path / "reports", timestamp="ack", diagnostic_detail=True
    )
    events = load_startup_trace(archive)
    diagnosis = json.loads(startup_json(events))
    written = next(event for event in events if event.get("name") == "surface_write")
    origin = next(event for event in events if event.get("name") == "cue_reconcile")
    assert written["args"]["parent_id"] == origin["args"]["span_id"]
    assert written["args"]["cue_revision"] == "42"
    assert written["args"]["round_trip_ms"] == 250.0
    assert diagnosis["interaction_latency"]["surface_write"]["p50_ms"] == 250.0
