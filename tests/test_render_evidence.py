"""Native request → fenced publication → producer summary → shareable report."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from saitenka_subtitles.geometry import FontProvider, FontSetup, RendererState
from test_report_metadata import _environment
from test_subtitle_pipeline import FakeCurrentRenderer, FakeGeometryBackend, request

from saitenka.app import render_evidence, report, telemetry
from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator


def _setup(monkeypatch, tmp_path):
    from saitenka import session

    config = _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "session_id", lambda: "runtime-test")
    (tmp_path / "overlay.log").write_text('{"session":"runtime-test"}\n')
    return config


def _export():
    telemetry.save_operation_summary()
    return json.loads(report.collect()["diagnostics/envelope.json"])


def _owner():
    return render_evidence.safe_runtime_configuration(render_evidence.registry.snapshot())[
        "owners"
    ][0]


@pytest.mark.timeout(5)
def test_renderer_toggle_preserves_requested_mode_and_post_draw_owner_in_zip(monkeypatch, tmp_path):
    from test_diagnostic_findings import read_envelope
    from test_native_subtitles import reader, settle_jobs

    _setup(monkeypatch, tmp_path)
    session, ipc, _backend = reader(tmp_path)
    try:
        presentation = session.graph.subtitle_presentation
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)

        presentation.toggle_renderer()
        telemetry.save_operation_summary()
        bundle = report.build_report_bundle(tmp_path / "reports")
        owners = read_envelope(bundle)["effective_runtime_configuration"]["owners"]
        history = owners[0]["renderer_selection"]["history"]

        assert (history[0]["pixel_owner"], history[0]["legacy_forced"]) == ("native", False)
        assert (history[-1]["pixel_owner"], history[-1]["legacy_forced"]) == ("legacy", True)
        assert history[-1]["revision"] > history[0]["revision"]
        assert "not pixel validation" in owners[0]["renderer_selection"]["scope"]
    finally:
        session.close()


def test_backend_request_parameters_survive_metadata_export(monkeypatch, tmp_path):
    config = _setup(monkeypatch, tmp_path)
    config.write_text("tip_scale = 9.0\n")
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    inputs = replace(
        request(0),
        frame_size=(3440, 1440),
        pixel_aspect=1.25,
        margins=(12, 13, 14, 15),
        renderer_state=RendererState(font_scale=1.5, line_spacing=0.25),
    )

    pipeline.render(inputs)
    payload = _export()

    evidence = payload["effective_runtime_configuration"]
    assert evidence["status"] == "partial"
    owner = evidence["owners"][0]
    fields = owner["configurations"][0]["fields"]
    assert (fields["frame_width"], fields["frame_height"]) == inputs.frame_size
    assert fields["pixel_aspect"] == 1.25
    assert fields["font_scale"] == 1.5
    assert fields["line_spacing"] == 0.25
    assert [fields[f"margin_{side}"] for side in ("top", "bottom", "left", "right")] == [
        12,
        13,
        14,
        15,
    ]
    assert {key: owner["published"][key] for key in owner["requested"]} == owner["requested"]
    assert owner["published"]["validation"]["tokens"] == 1
    assert payload["configuration"]["fields"]["tip_scale"]["value"] == 9.0
    assert payload["pixel_fidelity"]["status"] == "unknown"


@pytest.mark.parametrize(
    ("changes", "field", "before", "after"),
    [
        ({"frame_size": (3440, 1440)}, "frame_width", 1920, 3440),
        ({"renderer_state": RendererState(font_scale=1.5)}, "font_scale", 1.0, 1.5),
        ({"font_setup": FontSetup(font_provider=FontProvider.NONE)}, "font_provider", 1, 0),
        ({"attachments": (("PRIVATE.ttf", b"PRIVATE"),)}, "attachment_count", 0, 1),
    ],
)
def test_configuration_transition_preserves_both_revisions_in_zip(
    monkeypatch, tmp_path, changes, field, before, after
):
    import zipfile

    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    try:
        pipeline.render(request(0))
        generation = pipeline.invalidate()

        pipeline.render(replace(request(generation), **changes))
        telemetry.save_operation_summary()
        bundle = report.build_report_bundle(tmp_path / "reports", timestamp="transition")

        with zipfile.ZipFile(bundle) as archive:
            payload = json.loads(archive.read("diagnostics/envelope.json"))
            assert b"PRIVATE" not in b"".join(archive.read(name) for name in archive.namelist())
        owner = payload["effective_runtime_configuration"]["owners"][0]
        assert [(row["revision"], row["fields"][field]) for row in owner["configurations"]] == [
            (1, before),
            (2, after),
        ]
        assert owner["published"]["revision"] == 2
        assert owner["published"]["generation"] == generation
        assert payload["pixel_fidelity"]["status"] == "unknown"
    finally:
        pipeline.close()


def test_native_version_is_from_the_accepted_result_not_collector(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    class VersionedBackend(FakeGeometryBackend):
        def render(self, request):
            return replace(
                super().render(request), libass_version=0x01704000, mask_source="native-original"
            )

    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), VersionedBackend())
    pipeline.render(request(0))

    publication = _export()["effective_runtime_configuration"]["owners"][0]["published"]
    assert publication["libass_version"] == 0x01704000
    assert publication["mask_source"] == "native-original"
    assert publication["status"] == "retained"


def test_stale_publish_cannot_claim_new_configuration_has_rendered(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    backend = FakeGeometryBackend()
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    old = pipeline.prepare(request(0))
    assert old is not None
    generation = pipeline.invalidate()
    current = pipeline.prepare(replace(request(generation), frame_size=(3440, 1440)))
    assert current is not None

    accepted = pipeline.publish(old, backend.render(old.request))

    owner = _owner()
    assert not accepted
    assert owner["requested"]["revision"] == current.configuration_revision
    assert owner["published"]["status"] == "unknown"
    assert owner["generation"] == generation


def test_failed_current_work_clears_current_but_keeps_historical_publication(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.render(request(0))
    reservation = pipeline.reserve(0)
    assert reservation is not None

    pipeline.record_error(reservation, RuntimeError("private failure"))

    owner = _owner()
    assert owner["published"]["status"] == "unknown"
    assert owner["last_published"]["status"] == "retained"


def test_close_preserves_historical_configuration_without_claiming_current_pixels(
    monkeypatch, tmp_path
):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.render(request(0))

    pipeline.close()

    owner = _export()["effective_runtime_configuration"]["owners"][0]
    assert owner["closed"] is True
    assert owner["published"]["status"] == "unknown"
    assert owner["last_published"]["status"] == "retained"


def test_prefetch_cannot_replace_current_configuration_publication(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.render(request(0))

    pipeline.render_prefetch(replace(request(0, 1500), frame_size=(3440, 1440)))

    owner = _owner()
    assert owner["requested"]["revision"] == owner["published"]["revision"] == 1
    assert owner["configurations"][-1]["revision"] == 2


def test_same_configuration_across_cues_reuses_revision(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.render(request(0))
    generation = pipeline.invalidate()

    pipeline.render(request(generation, 1500))

    owner = _owner()
    assert len(owner["configurations"]) == 1
    assert owner["published"]["revision"] == 1
    assert owner["published"]["generation"] == generation


def test_renderer_history_is_bounded_and_unchanged_draws_do_not_evict(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    try:
        for owner in ["native", "legacy"] * 5:
            pipeline.record_pixel_owner(owner)
        for _ in range(20):
            pipeline.record_pixel_owner("legacy")

        selection = _export()["effective_runtime_configuration"]["owners"][0]["renderer_selection"]

        assert selection["evicted"] == 6
        assert [row["revision"] for row in selection["history"]] == [7, 8, 9, 10]
        assert [row["pixel_owner"] for row in selection["history"]] == ["native", "legacy"] * 2
    finally:
        pipeline.close()


def test_font_path_changes_advance_revision_without_exporting_paths_or_text(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    initial = replace(
        request(0),
        ass=b"SECRET-CUE",
        attachments=(("SECRET-ATTACHMENT", b"SECRET-FONT"),),
        font_setup=FontSetup(default_font="/private/secret-font-a", default_family="SECRET-FAMILY"),
    )
    pipeline.render(initial)

    pipeline.render(
        replace(
            initial,
            font_setup=FontSetup(
                default_font="/private/secret-font-b", default_family="SECRET-FAMILY"
            ),
        )
    )

    payload = _export()
    serialized = json.dumps(payload)
    assert "SECRET" not in serialized and "secret-font" not in serialized
    owner = payload["effective_runtime_configuration"]["owners"][0]
    assert owner["published"]["revision"] == 2
    assert owner["configurations"][-1]["fields"]["attachment_count"] == 1
    assert payload["effective_runtime_configuration"]["resolved_font_faces"] == "unknown"


def test_configuration_retention_accounts_for_evicted_revisions(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())

    for width in range(1000, 1010):
        pipeline.render(replace(request(0), frame_size=(width, 720)))

    owner = _owner()
    assert len(owner["configurations"]) == 4
    assert owner["configurations_evicted"] == 6
    assert owner["published"]["revision"] == 10


def test_multiple_geometry_owners_are_bounded_and_do_not_overwrite_each_other(
    monkeypatch, tmp_path
):
    _setup(monkeypatch, tmp_path)

    for width in range(1000, 1006):
        pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
        pipeline.render(replace(request(0), frame_size=(width, 720)))

    evidence = _export()["effective_runtime_configuration"]
    assert len(evidence["owners"]) == 4
    assert evidence["owners_evicted"] == 2
    assert [
        owner["configurations"][0]["fields"]["frame_width"] for owner in evidence["owners"]
    ] == [1002, 1003, 1004, 1005]


@pytest.mark.parametrize(
    "raw",
    [
        {"schema": True},
        {"schema": 2},
        {"schema": 1, "owners": "private"},
        {"schema": 1, "owners": [{}] * 5},
    ],
)
def test_invalid_runtime_evidence_cannot_claim_collected_configuration(raw):
    result = render_evidence.safe_runtime_configuration(raw)

    assert result["status"] in {"invalid", "unsupported-schema"}
    assert "owners" not in result


def test_conflicting_serialized_owners_cannot_establish_configuration(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for width in (1920, 3440):
        pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
        pipeline.render(replace(request(0), frame_size=(width, 1440)))
    raw = render_evidence.registry.snapshot()
    raw["owners"][1]["owner"] = raw["owners"][0]["owner"]

    result = render_evidence.safe_runtime_configuration(raw)

    assert result == {"status": "invalid"}


def test_export_allowlist_rejects_injected_freeform_configuration(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.render(request(0))
    raw = render_evidence.registry.snapshot()
    fields = raw["owners"][0]["configurations"][0]["fields"]
    fields.update(
        frame_width="PRIVATE", hinting={"PRIVATE": 1}, pixel_aspect=float("inf"), cue="PRIVATE"
    )

    result = render_evidence.safe_runtime_configuration(raw)

    assert "PRIVATE" not in json.dumps(result)
    sanitized = result["owners"][0]["configurations"][0]["fields"]
    assert sanitized["frame_width"] is None
    assert sanitized["hinting"] is None
    assert sanitized["pixel_aspect"] is None


def test_libass_feature_numbers_keep_their_meaning():
    inputs = replace(
        request(0), renderer_state=RendererState(features=((1, True), (2, False), (3, True)))
    )

    fields = render_evidence.configuration_fields(inputs)

    assert fields["feature_bidi_brackets"] is True
    assert fields["feature_whole_text_layout"] is False
    assert fields["feature_wrap_unicode"] is True


def test_stale_serialized_reference_is_not_current_publication(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.render(request(0))
    raw = render_evidence.registry.snapshot()
    raw["owners"][0]["generation"] = 1

    owner = render_evidence.safe_runtime_configuration(raw)["owners"][0]

    assert owner["published"]["status"] == "stale"
    assert owner["requested"]["status"] == "stale"
    assert owner["last_published"]["status"] == "retained"


@pytest.mark.timeout(5)
def test_observed_ultrawide_cue_exports_consumed_configuration(monkeypatch, tmp_path):
    from test_native_subtitles import reader, settle_jobs
    from util import record_spans

    _setup(monkeypatch, tmp_path)
    spans = record_spans(monkeypatch)
    session, ipc, backend = reader(tmp_path)
    ipc.props["osd-dimensions"] = {"w": 3440, "h": 1440, "mt": 10, "mb": 11, "ml": 0, "mr": 0}
    assert session.graph.presentation.refresh_osd()
    try:
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)
        payload = _export()
    finally:
        session.close()

    owner = payload["effective_runtime_configuration"]["owners"][0]
    fields = owner["configurations"][-1]["fields"]
    assert (
        (fields["frame_width"], fields["frame_height"])
        == backend.requests[-1].frame_size
        == (3440, 1440)
    )
    assert fields["margin_top"] == 10
    assert fields["margin_bottom"] == 11
    assert fields["source_kind"] == "authored-ass"
    assert fields["document"]["playresx"] == 1280
    assert fields["document"]["playresy"] == 720
    assert fields["document"]["style_count"] == 1
    assert fields["document"]["active_event_count"] == 1
    assert owner["published"]["status"] == "retained"
    render = next(span["attrs"] for span in spans if span["name"] == "subtitle_geometry_render")
    publication = next(
        span["attrs"] for span in spans if span["name"] == "subtitle_geometry_publish"
    )
    assert (
        render["configuration_revision"]
        == publication["configuration_revision"]
        == owner["published"]["revision"]
    )
    assert render["configuration_owner"] == publication["configuration_owner"] == owner["owner"]


@pytest.mark.timeout(5)
def test_metadata_only_bundle_explains_the_published_geometry_source(monkeypatch, tmp_path, capsys):
    from test_native_subtitles import reader, settle_jobs

    from saitenka.app.commands.diagnostics import subtitle_report

    _setup(monkeypatch, tmp_path)
    session, ipc, _backend = reader(tmp_path)
    try:
        session.graph.playback.observe("sub-text", "猫を見る")
        session.graph.cue.settle()
        settle_jobs(session, ipc)
        telemetry.save_operation_summary()

        bundle = report.build_report_bundle(tmp_path / "reports")

        assert subtitle_report(str(bundle)) == 0
        output = capsys.readouterr().out
        assert "bounded metadata history" in output
        assert "scan=shadow paint=shadow" in output
        assert "猫を見る" not in output
    finally:
        session.close()


def test_source_history_is_bounded_and_redacts_untrusted_values(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    for revision in range(40):
        pipeline.record_geometry_source(
            {
                "cue_revision": revision,
                "configured_source": "auto",
                "selected_source": "shadow",
                "scan_source": "shadow",
                "paint_source": "shadow",
                "reason": "/PRIVATE/path",
                "paint_reason": "PRIVATE TEXT",
                "paint_allowed": True,
                "generation": 0,
                "eligible_tokens": 1,
            }
        )

    payload = _export()

    history = payload["effective_runtime_configuration"]["owners"][0]["geometry_sources"]
    assert history["evicted"] == 8
    assert [row["cue_revision"] for row in history["history"]] == list(range(8, 40))
    assert "PRIVATE" not in json.dumps(payload)


def test_invalid_trace_is_not_hidden_by_metadata_source_history(monkeypatch, tmp_path, capsys):
    import zipfile

    from saitenka.app.commands.diagnostics import subtitle_report

    _setup(monkeypatch, tmp_path)
    pipeline = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    pipeline.record_geometry_source(
        {
            "configured_source": "auto",
            "selected_source": "shadow",
            "scan_source": "shadow",
            "paint_source": "none",
            "cue_revision": 1,
            "generation": 0,
            "eligible_tokens": 1,
            "paint_allowed": False,
            "paint_reason": "no-scan-regions",
            "reason": "layout-unsupported-render-mode",
        }
    )
    telemetry.save_operation_summary()
    bundle = report.build_report_bundle(tmp_path / "reports")
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr("trace.json", "{broken JSON")

    assert subtitle_report(str(bundle)) == 1

    assert "subtitle report unavailable" in capsys.readouterr().err
