"""Exported causal contracts across reused production workers."""

from __future__ import annotations

import json
import threading
import zipfile
from contextvars import ContextVar

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from saitenka import otel_metrics
from saitenka.app.otel_export import CTFSpanProcessor
from saitenka.app.telemetry import ActiveGate
from saitenka.runtime import Owner
from saitenka.runtime.jobs import JobLanePolicy, LocalJobLane
from saitenka.trace_analysis import parent_tree_health, tooltip_quality


@pytest.mark.timeout(5)
@pytest.mark.parametrize("fails", [False, True])
def test_reused_lane_exports_each_requests_parent_and_restores_context(
    monkeypatch, tmp_path, fails
):
    gate = ActiveGate()
    gate.set(value=True)
    path = tmp_path / "trace.json"
    processor = CTFSpanProcessor(path, gate, start_thread=False)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    user_context = ContextVar("locale", default="missing")
    observed = []

    def work(request, _cancelled):
        with otel_metrics.traced("work", request=request):
            observed.append(user_context.get())
            if fails:
                raise ValueError("controlled")

    with otel_metrics.traced("startup.reader_create"):
        lane = LocalJobLane("test", JobLanePolicy(1), work)
    try:
        for request in ("first", "second"):
            done = threading.Event()

            def completed(result, done=done):
                with otel_metrics.traced("completed", outcome=result.outcome.value):
                    done.set()

            token = user_context.set(request)
            try:
                with otel_metrics.traced(request):
                    assert lane.submit(
                        owner=Owner.SUBTITLE,
                        identity=request,
                        lane="test",
                        request=request,
                        on_finished=completed,
                    )
            finally:
                user_context.reset(token)
            assert done.wait(2)
    finally:
        lane.close()
        provider.shutdown()
    events = json.loads(path.read_text())["traceEvents"]
    parents = {event["name"]: event["args"]["span_id"] for event in events}
    work_events = [event for event in events if event["name"] == "work"]
    assert observed == ["first", "second"]
    assert [event["args"]["parent_id"] for event in work_events] == [
        parents["first"],
        parents["second"],
    ]
    assert {event["args"]["parent_id"] for event in events if event["name"] == "completed"} == {
        parents["first"],
        parents["second"],
    }
    assert not parent_tree_health(events)["suspected_startup_context_leaks"]


def test_tooltip_classification_preserves_unknown_and_mixed_views():
    events = [
        {"ph": "X", "name": "tip_compose", "args": {"kind": kind, "soft_reason": reason}}
        for kind, reason, count in (("base", "", 79), ("base", "warming", 33), ("nested", "", 13))
        for _ in range(count)
    ]
    events.append({"ph": "X", "name": "tip_compose", "args": {}})
    assert tooltip_quality(events) == {
        "base": {"crisp": 79, "soft": 33, "unknown": 0},
        "nested": {"crisp": 13, "soft": 0, "unknown": 0},
        "unknown": {"crisp": 0, "soft": 0, "unknown": 1},
    }


def test_late_async_child_is_not_a_structural_error_but_startup_leak_is_suspicious():
    def pair(name):
        return [
            {"name": name, "ph": "X", "ts": 0, "dur": 1, "args": {"span_id": "a"}},
            {
                "name": "work",
                "ph": "X",
                "ts": 2,
                "dur": 1,
                "args": {"span_id": "b", "parent_id": "a"},
            },
        ]

    healthy = parent_tree_health(pair("request"))
    assert healthy["late_children"] == ["b"]
    assert healthy["suspected_startup_context_leaks"] == []
    assert parent_tree_health(pair("startup.reader_create"))["suspected_startup_context_leaks"] == [
        "b"
    ]


def test_parent_cycle_does_not_label_its_noncyclic_descendant_as_a_cycle():
    events = [
        {"ph": "X", "args": {"span_id": child, "parent_id": parent}}
        for child, parent in (("tail", "a"), ("a", "b"), ("b", "a"))
    ]
    assert parent_tree_health(events)["cycles"] == ["a", "b"]


def test_malformed_exported_attributes_remain_unknown():
    events = [
        {"ph": "X", "name": "tip_compose", "args": []},
        {"ph": "X", "args": {"span_id": [], "parent_id": {}}},
    ]
    assert parent_tree_health(events)["missing_ids"] == 2
    assert tooltip_quality(events)["unknown"]["unknown"] == 1


def test_failed_crisp_submission_does_not_claim_an_acknowledged_upgrade(monkeypatch, tmp_path):
    from test_report import _hermetic

    from saitenka.app import report
    from saitenka.app.features.tooltip.quality import ViewQuality
    from saitenka.app.trace_report import load_startup_trace, startup_json

    config = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    config.write_text(
        f'[telemetry]\nenabled = true\nexport_dir = "{directory}"\n', encoding="utf-8"
    )
    gate = ActiveGate()
    gate.set(value=True)
    path = directory / "trace-1.json"
    provider = TracerProvider()
    provider.add_span_processor(CTFSpanProcessor(path, gate, start_thread=False))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    quality = ViewQuality()
    panel = object()
    soft = quality.submit(panel, (0, 100, 2), kind="base", state="soft", reason="warming", job_id=1)
    quality.settle(soft, accepted=True, state="soft")
    crisp = quality.submit(panel, (0, 100, 2), kind="base", state="crisp", reason="", job_id=1)

    quality.settle(crisp, accepted=False, state="crisp")
    quality.close()
    provider.shutdown()

    archive = report.build_report_bundle(tmp_path / "reports", diagnostic_detail=True)
    events = load_startup_trace(archive)
    diagnosis = json.loads(startup_json(events))
    end = next(event["args"] for event in events if event["name"] == "tooltip_quality_end")
    assert end["acknowledged_quality"] == "soft"
    assert end["upgrade_abandoned"]
    assert not any(
        event["name"] == "tooltip_quality_acknowledged" and event["args"]["quality"] == "crisp"
        for event in events
    )
    records = next(iter(diagnosis["tooltip_lifecycles"]["views"].values()))
    assert any(row.get("outcome") == "failed" and row.get("quality") == "crisp" for row in records)
    assert any(row.get("upgrade_abandoned") is True for row in records)
    assert "display unmeasured" in diagnosis["tooltip_lifecycles"]["endpoint"]


def test_old_viewport_ack_cannot_settle_a_new_scroll_destination(monkeypatch, tmp_path):
    from saitenka.app.features.tooltip.quality import ViewQuality

    gate = ActiveGate()
    gate.set(value=True)
    path = tmp_path / "trace.json"
    provider = TracerProvider()
    provider.add_span_processor(CTFSpanProcessor(path, gate, start_thread=False))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    quality = ViewQuality()
    panel = object()
    pending = quality.submit(panel, (0, 100, 2), kind="base", state="crisp", reason="", job_id=1)
    quality.prepare(panel, (100, 100, 2))

    quality.settle(pending, accepted=True, state="crisp")
    quality.close()
    provider.shutdown()

    events = json.loads(path.read_text())["traceEvents"]
    assert not any(event["name"] == "tooltip_quality_acknowledged" for event in events)
    end = next(event["args"] for event in events if event["name"] == "tooltip_quality_end")
    assert end["acknowledged_quality"] == "unknown" and end["upgrade_abandoned"]
    terminal = next(
        event["args"] for event in events if event["name"] == "tooltip_quality_submission"
    )
    assert terminal["outcome"] == "superseded"


@pytest.mark.parametrize("warm_kind", ["nested", "base"])
def test_mixed_view_quality_survives_production_export_and_bundle(monkeypatch, tmp_path, warm_kind):
    from test_render_cache import _nested_reader
    from test_report import _hermetic

    from saitenka.app import report
    from saitenka.app.features.tooltip import nested_popup, tooltip_panel

    config = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    config.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{directory}"\n')
    gate = ActiveGate()
    gate.set(value=True)
    provider = TracerProvider()
    processor = CTFSpanProcessor(directory / "trace-1.json", gate, start_thread=False)
    provider.add_span_processor(processor)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    reader = _nested_reader(two_words=True)
    try:
        reader.graph.tooltip.select(0)
        reader.graph.tooltip.show_tooltip(0)
        token = reader.graph.subtitle_presentation.cue.current.tokens[1]
        nested_popup.open_nested(
            reader.graph.tooltip.tip_ports,
            reader.graph.tooltip.panel_ports,
            token,
            token.surface,
            nested_popup.Anchor(300.0, 2000.0, 40.0),
        )
        nested = reader.graph.tooltip.surface_state().nest
        base = reader.graph.tooltip.surface_state().view
        assert nested.state is not None
        warmed = nested if warm_kind == "nested" else base
        assert warmed.state is not None
        warmed.state.warm_native_viewport(
            warmed.scroll, warmed.view_h, reader.graph.tooltip.scale().raster
        )
        tooltip_panel.apply_pending_crisp(reader.graph.tooltip.tip_ports, warmed)
        assert not warmed.crisp_pending
        assert (base if warm_kind == "nested" else nested).crisp_pending
        reader.graph.tooltip.hide_nested()
        reader.graph.tooltip.retire_state()
    finally:
        reader.close()
        provider.shutdown()

    archive = report.build_report_bundle(
        tmp_path / "reports", timestamp="mixed", diagnostic_detail=True
    )
    with zipfile.ZipFile(archive) as bundle:
        events = json.loads(bundle.read("telemetry/trace.json"))["traceEvents"]
    from saitenka.app.trace_report import load_startup_trace, startup_json

    diagnosis = json.loads(startup_json(load_startup_trace(archive)))
    cold_kind = "base" if warm_kind == "nested" else "nested"
    assert diagnosis["tooltip_quality"][cold_kind]["soft"] > 0
    assert diagnosis["tooltip_quality"][warm_kind]["crisp"] > 0
    assert len(diagnosis["tooltip_lifecycles"]["views"]) >= 2
    transitions = [
        event["args"] for event in events if event["name"] == "tooltip_quality_transition"
    ]
    nested_crisp = [
        event for event in transitions if event["kind"] == warm_kind and event["quality"] == "crisp"
    ]
    base_soft = [
        event for event in transitions if event["kind"] == cold_kind and event["quality"] != "crisp"
    ]
    assert nested_crisp and base_soft
    assert nested_crisp[0]["view_id"] != base_soft[0]["view_id"]
    assert any(
        event["args"]["upgrade_abandoned"]
        for event in events
        if event["name"] == "tooltip_quality_end"
    )
    assert any(
        event["args"]["outcome"] == "acknowledged"
        for event in events
        if event["name"] == "tooltip_quality_submission"
    )
