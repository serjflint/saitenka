import json
from dataclasses import replace

import pytest
from compare_cached_characters import geometry_request_for
from replay_render_configuration import configuration, replay, request_for_configuration
from saitenka_subtitles.geometry import RendererState
from synthetic_characters import SPEC, documents

from saitenka.app.render_evidence import GeometryEvidence, registry
from saitenka.app.subtitle_fonts import FontEnvironment


@pytest.fixture(autouse=True)
def enabled_evidence(monkeypatch, tmp_path):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from saitenka import otel_metrics
    from saitenka.app import telemetry
    from saitenka.app.otel_export import CTFSpanProcessor

    gate = otel_metrics.ActiveGate()
    gate.set(value=True)
    processor = CTFSpanProcessor(tmp_path / "trace.json", gate, start_thread=False)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    monkeypatch.setattr(otel_metrics, "span_gate", gate)
    monkeypatch.setattr(telemetry, "span_gate", gate)
    monkeypatch.setattr(telemetry, "_span_processor", processor)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    yield
    provider.shutdown()


def _envelope():
    return {"effective_runtime_configuration": registry.snapshot()}


def request():
    source = documents(json.loads(SPEC.read_text(encoding="utf-8")))["ass"]
    return geometry_request_for(source, 1500, (1280, 720), FontEnvironment())[0]


def test_replay_consumes_published_geometry_not_collector_config():
    evidence = GeometryEvidence()
    source = replace(
        request(),
        frame_size=(3440, 1440),
        storage_size=(1920, 1080),
        pixel_aspect=1.2,
        margins=(1, 2, 3, 4),
        renderer_state=RendererState(font_scale=1.25, line_spacing=0.5),
    )
    revision = evidence.describe(source)
    evidence.published(revision, 0, 1)

    recipe = configuration(_envelope(), owner=evidence.owner)
    replayed = request_for_configuration(
        source.native_ass.decode(), 1500, FontEnvironment(), recipe
    )

    assert replayed.frame_size == source.frame_size
    assert replayed.storage_size == source.storage_size
    assert replayed.renderer_state == source.renderer_state
    assert replayed.pixel_aspect == source.pixel_aspect
    assert replayed.margins == source.margins
    assert "resolved-font-faces" in recipe["unreproduced"]


@pytest.mark.parametrize("fault", ["stale", "evicted", "missing-field"])
def test_replay_refuses_missing_or_stale_configuration(fault):
    evidence = GeometryEvidence()
    revision = evidence.describe(request())
    evidence.published(revision, 0, 1)
    envelope = _envelope()
    selected = next(
        row
        for row in envelope["effective_runtime_configuration"]["owners"]
        if row["owner"] == evidence.owner
    )
    if fault == "stale":
        selected["generation"] = 1
    elif fault == "evicted":
        selected["configurations"] = []
    else:
        del selected["configurations"][0]["fields"]["font_scale"]

    with pytest.raises(ValueError, match=r"retained configuration|missing replay settings"):
        configuration(envelope, owner=evidence.owner)


def test_replay_refuses_oversized_canvas_before_native_render():
    evidence = GeometryEvidence()
    revision = evidence.describe(request())
    evidence.published(revision, 0, 1)
    recipe = configuration(_envelope(), owner=evidence.owner)
    recipe["fields"]["frame_width"] = 1_000_000
    recipe["fields"]["frame_height"] = 1_000_000
    with pytest.raises(ValueError, match="bounded diagnostic canvas"):
        request_for_configuration("", 1500, FontEnvironment(), recipe)


def test_synthetic_replay_executes_the_recorded_ultrawide_configuration():
    evidence = GeometryEvidence()
    revision = evidence.describe(replace(request(), frame_size=(3440, 1440)))
    evidence.published(revision, 0, 1)
    recipe = configuration(_envelope(), owner=evidence.owner)

    result = replay(recipe)

    assert len(result["rows"]) == 12
    assert {row["verdict"] for row in result["rows"]} == {"inconclusive"}
    assert all(row["found_tokens"] > 0 for row in result["rows"])
    assert {row["reason"] for row in result["rows"]} == {"pixel-comparison-retired"}
    assert "independent mpv qualification not run" in result["qualification"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("font_scale", 1e9),
        ("pixel_aspect", 1e9),
        ("blur", 1e9),
        ("hinting", 0.5),
        ("margin_top", 0.5),
    ],
)
def test_replay_refuses_unbounded_or_wrongly_typed_native_settings(key, value):
    evidence = GeometryEvidence()
    revision = evidence.describe(request())
    evidence.published(revision, 0, 1)
    recipe = configuration(_envelope(), owner=evidence.owner)
    recipe["fields"][key] = value

    with pytest.raises(ValueError, match="replay"):
        request_for_configuration("", 1500, FontEnvironment(), recipe)
