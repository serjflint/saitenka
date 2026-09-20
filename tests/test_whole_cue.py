from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from saitenka_subtitles import (
    SubtitleTrackId,
    TokenAnnotation,
    authored_ass_rows_at,
    prepare_ass_hit_map_frame,
)
from saitenka_subtitles.geometry import PaintQualification
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_subtitles.whole_cue import FillLayer, WholeCue, compose, osd_payload, osd_template
from test_ass_geometry import ASS
from test_libass_geometry_backend import _recording_factory, probeable_request
from test_overprint import Style, draw_request

from saitenka.app.config import subtitle_geometry_options
from saitenka.app.subtitle_render import whole_cue_device
from saitenka.app.subtitles import WordBox


@pytest.mark.parametrize(
    "mode", ["legacy", "whole-cue-auto", "whole-cue-osd", "whole-cue-overpaint", "boxes-only"]
)
def test_coloring_option_roundtrips(mode):
    assert subtitle_geometry_options({"subtitle_geometry": {"coloring": mode}}).coloring == mode


def test_coloring_option_preserves_default_and_rejects_typos():
    assert subtitle_geometry_options({}).coloring == "legacy"
    with pytest.raises(ValueError, match="coloring"):
        subtitle_geometry_options({"subtitle_geometry": {"coloring": "whole-cue"}})


def test_whole_cue_backend_retains_layers_without_rendering_native_or_probes():
    created = []
    backend = LibassGeometryBackend(renderer_factory=_recording_factory(created))
    request = replace(
        probeable_request(),
        native_ass=b"native reference",
        coloring="whole-cue-auto",
        whole_cue=WholeCue(),
        paint_qualification=PaintQualification.STATIC,
    )
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()
    assert created[0].documents == [request.ass]
    assert len(created[0].calls) == 1
    assert snapshot.whole_cue is not None
    assert snapshot.whole_cue.layers == (
        FillLayer(0, 10, 20, 1, 1, b"\xff"),
        FillLayer(1, 30, 40, 1, 1, b"\xff"),
    )
    assert snapshot.coverage_bytes == 2
    assert snapshot.without_coverage().coverage_bytes == 0
    assert snapshot.without_coverage().tokens == snapshot.tokens


def test_layer_recoloring_preserves_coverage_and_omits_unselected_text():
    cue = WholeCue(
        layers=(FillLayer(0, 5, 6, 2, 1, b"\x80\xff"), FillLayer(1, 20, 6, 1, 1, b"\xff"))
    )
    red = compose(cue, ((0, 0xFF0000),))
    blue = compose(cue, ((0, 0x0000FF),))
    assert red is not None and blue is not None
    assert (red.x, red.y, red.rgba.shape) == (5, 6, (1, 2, 4))
    assert red.rgba.tolist() == [[[255, 0, 0, 128], [255, 0, 0, 255]]]
    assert np.array_equal(red.rgba[..., 3], blue.rgba[..., 3])
    assert blue.rgba[0, 0].tolist() == [0, 0, 255, 128]
    assert compose(cue, ()) is None


def test_overlapping_layers_follow_source_over_order():
    cue = WholeCue(layers=(FillLayer(0, 0, 0, 1, 1, b"\xff"), FillLayer(1, 0, 0, 1, 1, b"\x80")))
    image = compose(cue, ((0, 0xFF0000), (1, 0x0000FF)))
    assert image is not None
    assert image.rgba.tolist() == [[[127, 0, 128, 255]]]


def test_distant_layers_exceeding_composite_budget_fall_back_to_boxes():
    cue = WholeCue(
        layers=(FillLayer(0, 0, 0, 1, 1, b"\xff"), FillLayer(1, 3999, 2999, 1, 1, b"\xff"))
    )
    request = replace(
        draw_request(styles=[], boxes=[]), whole_cue=cue, coloring="whole-cue-overpaint"
    )
    assert whole_cue_device(request) == ("none", "composite-budget")
    assert compose(cue, ((0, 0xFF0000), (1, 0x00FF00))) is None


@pytest.mark.parametrize("image_type", [0, 1, 2])
def test_later_unannotated_fill_or_effect_refuses_paint_but_retains_scanning(image_type):
    from test_libass_geometry_backend import FakeRenderer, Layer, Result

    layers = Result(
        (
            Layer(1, 1, b"\xff", 0x01020300, 10, 20),
            Layer(1, 1, b"\xff", 0xFFFFFF00, 10, 20, image_type),
        )
    )
    backend = LibassGeometryBackend(renderer_factory=lambda *_a, **_kw: FakeRenderer(layers))
    request = replace(
        probeable_request(palette_size=1),
        coloring="whole-cue-auto",
        whole_cue=WholeCue(),
        paint_qualification=PaintQualification.STATIC,
    )
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()
    assert snapshot.paint_qualification is PaintQualification.OCCLUDED
    assert [token.token_index for token in snapshot.tokens] == [0]


def prepared_source(*, kerning="yes", wrap="2"):
    source = ASS.replace(
        b"PlayResY: 720", f"PlayResY: 720\nKerning: {kerning}\nWrapStyle: {wrap}".encode()
    ).replace("猫を見る".encode(), r"{\pos(640,600)}猫を見る".encode())
    track = SubtitleTrackId("test")
    rows, text = authored_ass_rows_at(source, track, 1500)
    prepared = prepare_ass_hit_map_frame(
        source,
        track,
        active_rows=rows,
        text=text,
        tokens=(TokenAnnotation(0, 0, 1), TokenAnnotation(2, 2, 4)),
    )
    return source, prepared


def test_osd_keeps_complete_text_and_positions_with_unselected_paint_hidden():
    source, prepared = prepared_source()
    template = osd_template(source, prepared, fonts_blocked=False)
    payload = osd_payload(template, ((0, 0xFF0000),))
    assert template.osd_reason == "eligible"
    assert r"\pos(640,600)" in payload
    assert r"\1c&H0000FF&" in payload
    assert r"\1a&HFF&" in payload
    assert payload.endswith("を見る")
    assert osd_payload(template, ()) == ""


@pytest.mark.parametrize(
    ("kerning", "wrap", "fonts", "reason"),
    [
        ("no", "2", False, "kerning"),
        ("yes", "0", False, "wrapping"),
        ("yes", "2", True, "font-access"),
    ],
)
def test_osd_rejects_unreproduced_document_inputs(kerning, wrap, fonts, reason):
    source, prepared = prepared_source(kerning=kerning, wrap=wrap)
    assert osd_template(source, prepared, fonts_blocked=fonts).osd_reason == reason


@pytest.mark.parametrize(
    ("mode", "reason", "shaper", "device"),
    [
        ("whole-cue-auto", "eligible", "complex", "overprint"),
        ("whole-cue-auto", "kerning", "complex", "overpaint"),
        ("whole-cue-auto", "eligible", "simple", "overpaint"),
        ("whole-cue-overpaint", "eligible", "complex", "overpaint"),
        ("whole-cue-osd", "kerning", "complex", "none"),
        ("boxes-only", "eligible", "complex", "none"),
    ],
)
def test_whole_cue_fallback_matrix(mode, reason, shaper, device):
    source, prepared = prepared_source()
    cue = replace(
        osd_template(source, prepared, fonts_blocked=False),
        layers=(FillLayer(0, 0, 0, 1, 1, b"\xff"),),
        osd_reason=reason,
    )
    request = replace(
        draw_request(styles=[Style((255, 0, 0, 255))], boxes=[WordBox(0, 0, 0, 1, 1)]),
        whole_cue=cue,
        coloring=mode,
        osd_shaper=shaper,
    )
    assert whole_cue_device(request)[0] == device
    assert whole_cue_device(replace(request, paint_allowed=False, paint_reason="karaoke")) == (
        "none",
        "karaoke" if mode != "boxes-only" else "boxes-only",
    )


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    ("mode", "painted"),
    [
        ("whole-cue-auto", True),
        ("whole-cue-overpaint", True),
        ("whole-cue-osd", False),
        ("boxes-only", False),
    ],
)
def test_observed_cue_uses_optional_coloring_without_changing_scan_boxes(tmp_path, mode, painted):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords
    from test_native_subtitles import presented_overpaints, reader, settle_jobs

    from saitenka.app.scoring import Coloring

    result, ipc, backend = reader(
        tmp_path, coloring=mode, scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])))
    )
    try:
        result.graph.playback.observe("sub-text", "猫を見る")
        result.graph.cue.settle()
        settle_jobs(result, ipc)
        assert result.graph.subtitle_presentation.cue.current.boxes
        assert bool(presented_overpaints(ipc)) is painted
        assert backend.requests[-1].coloring == mode
        assert backend.requests[-1].keep_coverage is False
        assert ipc.props["sub-visibility"] is True
        assert not any(command[0] == "sub-add" for command in ipc.commands)
    finally:
        result.close()


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("mode", ["whole-cue-auto", "whole-cue-overpaint", "whole-cue-osd"])
def test_explicit_shadow_keeps_boxes_when_backend_refuses_paint(tmp_path, monkeypatch, mode):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords
    from test_native_subtitles import FakeBackend, presented_overpaints, reader, settle_jobs

    from saitenka.app.scoring import Coloring

    render = FakeBackend.render
    monkeypatch.setattr(
        FakeBackend,
        "render",
        lambda self, request: replace(
            render(self, request), paint_qualification=PaintQualification.OCCLUDED
        ),
    )
    result, ipc, _backend = reader(
        tmp_path,
        geometry_source="shadow",
        coloring=mode,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"]))),
    )
    try:
        result.graph.playback.observe("sub-text", "猫を見る")
        result.graph.cue.settle()
        settle_jobs(result, ipc)
        request = result.graph.cue.draw_request()
        assert request.boxes
        assert not request.paint_allowed
        assert not presented_overpaints(ipc)
        from saitenka.app.render_evidence import registry, safe_runtime_configuration

        history = safe_runtime_configuration(registry.snapshot())["owners"][-1]["whole_cue"][
            "history"
        ]
        decisions = [row for row in history if row.get("event") == "decision"]
        assert decisions[-1]["reason"] == "overlapping-paint"
        assert decisions[-1]["device"] == "none"
    finally:
        result.close()


def test_whole_event_osd_keeps_independent_underline():
    from test_overprint import FakeSurfaces

    from saitenka.app.subtitle_render import NativeVisibleRenderer

    source, prepared = prepared_source()
    cue = osd_template(source, prepared, fonts_blocked=False)
    request = replace(
        draw_request(
            styles=[Style((255, 0, 0, 255), underline=(0, 255, 0, 255))],
            boxes=[WordBox(0, 10, 20, 30, 40)],
        ),
        coloring="whole-cue-osd",
        whole_cue=cue,
        osd_shaper="complex",
    )
    payload, resolution = NativeVisibleRenderer(coloring="whole-cue-osd")._draw_whole_cue(
        request, FakeSurfaces()
    )
    assert r"\pos(640,600)" in payload
    assert r"\p1" in payload
    assert r"\1c&H00FF00&" in payload
    assert resolution == (1280, 720)


def test_native_scan_boxes_do_not_rebuild_prepared_shadow_paint(monkeypatch):
    from saitenka_subtitles import decoration, whole_cue

    from saitenka.app.subtitle_render import NativeVisibleRenderer

    source, prepared = prepared_source()
    request = replace(
        draw_request(
            styles=[Style((255, 0, 0, 255), underline=(0, 255, 0, 255))],
            boxes=[WordBox(0, 10, 20, 30, 40)],
        ),
        coloring="whole-cue-osd",
        whole_cue=osd_template(source, prepared, fonts_blocked=False),
        osd_shaper="complex",
    )
    renderer = NativeVisibleRenderer(coloring="whole-cue-osd")
    expected = renderer.prepare_osd(request)

    def forbidden(*_args):
        pytest.fail("native scan geometry rebuilt the prepared paint artifact")

    monkeypatch.setattr(whole_cue, "osd_payload", forbidden)
    monkeypatch.setattr(decoration, "payload", forbidden)
    published = renderer.prepare_osd(
        replace(request, boxes=[WordBox(0, 11, 21, 31, 41)], paint_boxes=request.boxes)
    )
    assert published == expected


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_empty_observation_retires_whole_cue_raster(tmp_path):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords
    from test_native_subtitles import presented_overpaints, reader, settle_jobs

    from saitenka.app.overlay_ids import OverlayId
    from saitenka.app.scoring import Coloring

    result, ipc, _backend = reader(
        tmp_path,
        coloring="whole-cue-overpaint",
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"]))),
    )
    try:
        result.graph.playback.observe("sub-text", "猫を見る")
        result.graph.cue.settle()
        settle_jobs(result, ipc)
        assert presented_overpaints(ipc)
        ipc.commands.clear()

        result.graph.playback.observe("sub-text", "")
        result.graph.cue.settle()
        settle_jobs(result, ipc)

        assert ("overlay-remove", OverlayId.OVERPAINT) in ipc.commands
        assert not presented_overpaints(ipc)
        assert not result.graph.subtitle_presentation.cue.current.boxes
    finally:
        result.close()


@pytest.mark.parametrize(
    ("blocked", "reason"),
    [(frozenset({"arial"}), "font-access"), (frozenset({"unused attachment"}), "eligible")],
)
def test_only_active_font_families_block_whole_cue_osd(blocked, reason):
    source, prepared = prepared_source()
    cue = osd_template(
        source, prepared, fonts_blocked=False, blocked_families=blocked, frame=(1280, 720)
    )
    assert cue.osd_reason == reason


@pytest.mark.parametrize("reason", ["missing", "missing-coherent-evidence", "no-scan-regions"])
def test_unprepared_whole_cue_is_pending_not_a_coloring_policy_refusal(reason):
    request = replace(
        draw_request(styles=[Style((255, 0, 0, 255))], boxes=[]),
        coloring="whole-cue-osd",
        paint_allowed=False,
        paint_reason=reason,
    )
    assert whole_cue_device(request) == ("none", "pending-whole-cue")
    assert whole_cue_device(replace(request, coloring="boxes-only")) == ("none", "boxes-only")
    assert whole_cue_device(replace(request, paint_reason="scan-only-policy")) == (
        "none",
        "scan-only-policy",
    )
