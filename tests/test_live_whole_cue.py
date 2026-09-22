"""Whole-event raster equivalence and optional modes through the real presentation pipeline."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from live_harness import LayoutLiveOptions, live_reader, poll_until
from PIL import Image, ImageFilter
from saitenka_subtitles import (
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    authored_ass_rows_at,
    prepare_ass_hit_map_frame,
)
from saitenka_subtitles.geometry import FontSetup, PaintQualification
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_subtitles.whole_cue import WholeCue, compose, osd_payload, osd_template
from test_ass_geometry import ASS
from test_whole_cue import prepared_source
from util import record_spans

from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(30),
    pytest.mark.skipif(
        not os.environ.get("SAITENKA_LIVE"), reason="requires live font/display environment"
    ),
]


def _character_masks():
    path = Path(__file__).resolve().parents[1] / "tools/character_masks.py"
    spec = importlib.util.spec_from_file_location("character_masks", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("delay", [0.0, 1.25])
@pytest.mark.parametrize(
    "coloring",
    [
        pytest.param("whole-cue-auto", marks=pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)),
        pytest.param("whole-cue-osd", marks=pytest.mark.mpv_min("0.41")),
    ],
)
@pytest.mark.usefixtures("enabled_telemetry")
def test_rapid_indexed_seeks_land_on_the_prepared_cue_with_subtitle_delay(
    monkeypatch, delay, coloring
):
    from test_cue_color_timeline import _coloring

    from saitenka.app.subtitle_intents import SeekCue

    spans = record_spans(monkeypatch)
    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        cues=((0.0, 2.0, "猫を見る"), (3.0, 5.0, "犬も見る"), (6.0, 8.0, "鳥を見た")),
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            (
                "--sub-ass-override=no",
                "--blend-subtitles=no",
                f"--sub-delay={delay}",
                *(("--osd-shaper=complex",) if coloring == "whole-cue-osd" else ()),
            ),
            coloring=coloring,
            start_seconds=0.5 + delay,
        ),
    ) as (_tmp, session, ipc):
        poll_until(
            session,
            lambda: (
                session.graph.subtitle_presentation.native.worker.stats.prefetch_cache_entries >= 2
            ),
            "navigation targets were not prepared",
        )
        spans.clear()

        for _ in range(2):
            session.graph.subtitle_navigation.seek(SeekCue(1, session.graph.cue.revision))

        assert session.graph.playback.cue.text == "鳥を見た"
        poll_until(
            session,
            lambda: ipc.query("sub-text") == "鳥を見た",
            "mpv chose a different destination",
        )
        poll_until(
            session,
            lambda: (
                session.graph.subtitle_presentation.color_telemetry.cue_start_ms == 6000
                and session.graph.subtitle_presentation.color_telemetry.current is not None
                and session.graph.subtitle_presentation.color_telemetry.current.snapshot(0).status
                == "complete"
                and session.graph.cue.draw_request().text == "鳥を見た"
                and session.graph.cue.draw_request().boxes
            ),
            "destination color was not acknowledged",
        )
        assert session.graph.cue.draw_request().boxes
        assert not any(s["name"] == "subtitle_color_target_corrected" for s in spans)


@pytest.mark.parametrize(
    ("frame", "margins"), [((1280, 720), (0, 0, 0, 0)), ((3024, 1898), (98, 99, 0, 0))]
)
@pytest.mark.parametrize(
    "raw",
    [
        r"{\pos(640,600)}猫を見る",
        r"{\pos(640,600)}猫{\r}を見る",
        r"{\pos(640,600)}猫{\rDefault}を見る",
        r"{\pos(640,600)\fsp2\fscx90}猫を見る",
        r"{\pos(640,600)}猫\Nを見る",
        (
            r"{\pos(640,400)}猫"
            "\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
            r"{\pos(640,600)}犬"
        ),
        "猫を見る",
    ],
)
def test_osd_expansion_matches_whole_authored_cue(frame, margins, raw):
    source = ASS.decode().replace("猫を見る", raw).encode()
    track = SubtitleTrackId("expansion")
    rows, text = authored_ass_rows_at(source, track, 1500)
    prepared = prepare_ass_hit_map_frame(
        source,
        track,
        active_rows=rows,
        text=text,
        tokens=tuple(TokenAnnotation(i, i, i + 1) for i, c in enumerate(text) if not c.isspace()),
    )
    cue = osd_template(source, prepared, fonts_blocked=False, frame=frame, margins=margins)
    request = GeometryRequest(
        0,
        track,
        prepared.frame_id,
        1500,
        frame,
        (1920, 1080),
        prepared.ass,
        margins=margins,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
        coloring="whole-cue-osd",
        whole_cue=cue,
        paint_qualification=prepared.paint_qualification,
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()
    assert snapshot.whole_cue.osd_reason == "eligible"
    assert dict(snapshot.whole_cue.evidence)["comparison"] == "exact"


def test_occluded_lower_event_keeps_scanning_without_coloring():
    source = ASS.decode().split("Dialogue:")[0] + (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\pos(640,600)}猫\n"
        "Dialogue: 1,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\pos(640,600)}猫\n"
    )
    track = SubtitleTrackId("occlusion")
    rows, text = authored_ass_rows_at(source.encode(), track, 1500)
    prepared = prepare_ass_hit_map_frame(
        source.encode(), track, active_rows=rows, text=text, tokens=(TokenAnnotation(0, 0, 1),)
    )
    request = GeometryRequest(
        0,
        track,
        prepared.frame_id,
        1500,
        (1280, 720),
        (1280, 720),
        prepared.ass,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
        coloring="whole-cue-auto",
        whole_cue=WholeCue(),
        paint_qualification=prepared.paint_qualification,
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()
    assert snapshot.paint_qualification is PaintQualification.OCCLUDED
    assert len(snapshot.tokens) == 1


@pytest.mark.parametrize("frame", [(1280, 720), (1920, 1080)])
def test_qualified_osd_compiler_matches_authored_fill_at_two_sizes(frame):
    import pysubs2

    libasslite = pytest.importorskip("libasslite")
    source, prepared = prepared_source()
    cue = osd_template(source, prepared, fonts_blocked=False)
    payload = osd_payload(cue, ((0, 0x00FF00), (2, 0x00FF00)))
    osd = pysubs2.SSAFile()
    osd.info.update(PlayResX="1280", PlayResY="720", Kerning="yes", WrapStyle="1")
    osd.events = [pysubs2.SSAEvent(start=0, end=5000, text=payload)]
    reference = source.decode().replace(
        "猫を見る", r"{\1a&H00&\1c&H00FF00&\3a&HFF&\4a&HFF&}猫{\1a&HFF&}を{\1a&H00&}見る"
    )

    def pixels(document):
        renderer = libasslite.AssRenderer(document, default_family="sans-serif")
        try:
            layers = renderer.render(1500, frame, frame).layers
        finally:
            renderer.close()
        output = Image.new("RGBA", frame, (33, 71, 109, 255))
        for layer in layers:
            if layer.color & 255 == 255:
                continue
            tile = Image.new(
                "RGBA",
                (layer.width, layer.height),
                (layer.color >> 24, (layer.color >> 16) & 255, (layer.color >> 8) & 255, 0),
            )
            tile.putalpha(Image.frombytes("L", tile.size, layer.bitmap))
            output.alpha_composite(tile, (layer.dst_x, layer.dst_y))
        return np.asarray(output)

    assert np.array_equal(pixels(osd.to_string("ass").encode()), pixels(reference.encode()))


@pytest.mark.parametrize(
    ("text", "boundary"), [("猫を見る", 2), ("office AVATAR", 6), ("a\u0308bc", 2)]
)
@pytest.mark.parametrize("selected", [(0,), (0, 1)])
def test_marker_layers_preserve_opaque_color_and_final_event_geometry(text, boundary, selected):
    libasslite = pytest.importorskip("libasslite")

    source = (
        ASS.decode().replace("猫を見る", text).replace("YCbCr Matrix: TV.601", "YCbCr Matrix: None")
    )
    source = source.replace("0,100,100,0,0,1,2,1,2", "0,100,100,0,0,1,0,0,2").encode()
    track = SubtitleTrackId("differential")
    rows, semantic = authored_ass_rows_at(source, track, 1500)
    prepared = prepare_ass_hit_map_frame(
        source,
        track,
        active_rows=rows,
        text=semantic,
        tokens=(TokenAnnotation(0, 0, boundary), TokenAnnotation(1, boundary, len(text))),
    )
    request = GeometryRequest(
        0,
        track,
        prepared.frame_id,
        1500,
        (1280, 720),
        (1280, 720),
        prepared.ass,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
        native_ass=source,
        coloring="whole-cue-overpaint",
        whole_cue=WholeCue(),
        font_setup=FontSetup(default_family="sans-serif"),
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()
    assert snapshot.whole_cue is not None
    actual = compose(snapshot.whole_cue, tuple((index, 0x00FF00) for index in selected))
    assert actual is not None
    assert np.all(actual.rgba[actual.rgba[..., 3] > 0, :3] == (0, 255, 0))

    # Independent final-color document, without marker colors or token-derived bounds.
    raw = text if len(selected) == 2 else text[:boundary] + r"{\1a&HFF&}" + text[boundary:]
    direct = source.decode().replace(text, r"{\1c&H00FF00&\3a&HFF&\4a&HFF&}" + raw).encode()
    renderer = libasslite.AssRenderer(direct, default_family="sans-serif")
    try:
        reference = renderer.render(1500, (1280, 720), (1280, 720))
    finally:
        renderer.close()
    expected_alpha = np.zeros((720, 1280), dtype=np.uint8)
    for layer in reference.layers:
        if layer.image_type != 0 or layer.color & 255 == 255:
            continue
        source_alpha = np.frombuffer(layer.bitmap, dtype=np.uint8).reshape(
            layer.height, layer.width
        )
        region = expected_alpha[
            layer.dst_y : layer.dst_y + layer.height,
            layer.dst_x : layer.dst_x + layer.width,
        ]
        region[:] = source_alpha + region.astype(np.uint16) * (255 - source_alpha) // 255
    observed_alpha = np.zeros_like(expected_alpha)
    observed_alpha[
        actual.y : actual.y + actual.rgba.shape[0],
        actual.x : actual.x + actual.rgba.shape[1],
    ] = actual.rgba[..., 3]
    result = _character_masks().compare_mask(expected_alpha, observed_alpha, expected_alpha)
    assert result["verdict"] == "passed"


@pytest.mark.parametrize(
    ("mode", "device"),
    [
        pytest.param("whole-cue-auto", "overprint", marks=pytest.mark.mpv_min("0.41")),
        pytest.param("whole-cue-auto", "overpaint", marks=pytest.mark.mpv_min("0.41")),
        pytest.param(
            "whole-cue-overpaint", "overpaint", marks=pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)
        ),
        pytest.param("whole-cue-osd", "overprint", marks=pytest.mark.mpv_min("0.41")),
        pytest.param("boxes-only", "none", marks=pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)),
    ],
)
@pytest.mark.usefixtures("enabled_telemetry")
def test_optional_coloring_modes_upload_or_keep_boxes(mode, device, monkeypatch):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    spans = record_spans(monkeypatch)
    shaper = "complex" if device == "overprint" else "simple"
    with live_reader(
        native_visible=True,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])), Palette()),
        cues=((0.0, 8.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            (
                "--sub-ass-override=no",
                "--blend-subtitles=no",
                "--osd-level=0",
                "--geometry=1280x720",
                "--hidpi-window-scale=no",
                "--vo=gpu",
                "--gpu-sw=yes",
                *(
                    (f"--osd-shaper={shaper}",)
                    if mode == "whole-cue-auto" or device == "overprint"
                    else ()
                ),
            ),
            coloring=mode,
        ),
    ) as (tmp, session, ipc):
        presentation = session.graph.subtitle_presentation
        poll_until(
            session,
            lambda: any(
                row["name"] == "subtitle_whole_cue" and row["attrs"].get("device") == device
                for row in spans
            ),
            "new coloring mode did not select its device",
        )
        assert presentation.cue.current.boxes
        assert ipc.query("sub-visibility") is True
        if device != "none":
            poll_until(
                session,
                lambda: any(
                    row["name"] == "subtitle_color_ack"
                    and row["attrs"].get("accepted") is True
                    and row["attrs"].get("device") == device
                    for row in spans
                ),
                "color upload never acknowledged",
            )
        path = tmp / "colored.png"
        assert ipc.command("screenshot-to-file", str(path), "window")["error"] == "success"
        colored = np.asarray(Image.open(path).convert("RGB"))
        presentation.clear_pixels()
        assert (
            ipc.command("screenshot-to-file", str(tmp / "plain.png"), "window")["error"]
            == "success"
        )
        plain = np.asarray(Image.open(tmp / "plain.png").convert("RGB"))
        assert bool(np.any(colored != plain)) is (device != "none")


@pytest.mark.parametrize("blur", [0, 2])
@pytest.mark.mpv_min("0.41")
def test_osd_pixels_follow_qualified_shadow_with_secondary_translation(blur):
    from test_cue_color_timeline import _coloring

    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        cues=((0.0, 8.0, r"{\fs96\b1}猫を見る"),),
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            (
                "--sub-ass-override=no",
                "--blend-subtitles=no",
                "--osd-level=0",
                "--geometry=1280x720",
                "--hidpi-window-scale=no",
                "--vo=gpu",
                "--gpu-sw=yes",
                "--osd-shaper=complex",
                f"--osd-blur={blur}",
            ),
            coloring="whole-cue-osd",
        ),
    ) as (tmp, session, ipc):
        poll_until(
            session,
            lambda: session.graph.cue.draw_request().whole_cue is not None,
            "whole cue never prepared",
        )
        request = session.graph.cue.draw_request()
        assert request.whole_cue.osd_reason == "eligible"
        primary = ipc.query("sid")
        translation = tmp / "translation.srt"
        translation.write_text("1\n00:00:00,000 --> 00:00:08,000\nTranslation\n")
        assert ipc.command("sub-add", str(translation), "auto")["error"] == "success"
        secondary = ipc.query("track-list")[-1]["id"]
        assert ipc.command("set_property", "secondary-sid", secondary)["error"] == "success"
        session.graph.subtitle_presentation.clear_pixels()
        visible = False
        assert ipc.command("set_property", "sub-visibility", visible)["error"] == "success"
        assert (
            ipc.command("screenshot-to-file", str(tmp / "base.png"), "window")["error"] == "success"
        )
        base = np.asarray(Image.open(tmp / "base.png").convert("RGB"))
        colors = tuple((layer.token, 0x00FF00) for layer in request.whole_cue.layers)
        payload = osd_payload(request.whole_cue, colors)
        assert (
            ipc.command("osd-overlay", 2001, "ass-events", payload, *request.whole_cue.resolution)[
                "error"
            ]
            == "success"
        )
        assert (
            ipc.command("screenshot-to-file", str(tmp / "osd.png"), "window")["error"] == "success"
        )
        actual = np.asarray(Image.open(tmp / "osd.png").convert("RGB"))
        expected = compose(request.whole_cue, colors)
        alpha = np.zeros(base.shape[:2], np.uint8)
        h, w = expected.rgba.shape[:2]
        alpha[expected.y : expected.y + h, expected.x : expected.x + w] = expected.rgba[..., 3]
        Image.fromarray(alpha).save(tmp / "expected-alpha.png")
        changed = np.any(actual != base, axis=2)
        assert np.any(alpha == 255)
        # The installed and mpv libass builds differ at antialiased edges.
        support = np.asarray(Image.fromarray(alpha).filter(ImageFilter.MaxFilter(3))) > 0
        interior = np.asarray(Image.fromarray(alpha).filter(ImageFilter.MinFilter(3))) == 255
        assert np.any(interior)
        assert not np.any(changed & ~support)
        assert np.all(actual[interior] == (0, 255, 0))
        assert np.any(changed & ~np.roll(support, 3, axis=1))
        assert ipc.query("sid") == primary
        assert ipc.query("secondary-sid") == secondary


@pytest.mark.parametrize(
    ("osd_access", "kerning", "reason"),
    [(False, "yes", "font-access"), (True, "yes", "eligible"), (True, "no", "shape-mismatch")],
)
@pytest.mark.mpv_min("0.41")
def test_unique_attachment_font_requires_osd_access(tmp_path, osd_access, kerning, reason):
    import subprocess

    import live_harness
    from test_cue_color_timeline import _coloring

    font_tools = pytest.importorskip("fontTools.ttLib")
    alias = "SaitenkaWholeCueUniqueAttachment"
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    font_path = fonts / "unique.ttf"
    with font_tools.TTFont("src/saitenka/assets/fonts/NotoSans.ttf") as font:
        for record in font["name"].names:
            if record.nameID in {1, 4, 6, 16}:
                record.string = alias.encode(record.getEncoding())
        font.save(font_path)
    cues = ((0.0, 8.0, "AVATAR To"),)
    clip, _ = live_harness.make_clip_and_sub(tmp_path, cues)
    attached = tmp_path / "attached.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(clip),
            "-c",
            "copy",
            "-attach",
            str(font_path),
            "-metadata:s:t",
            "mimetype=font/ttf",
            str(attached),
        ],
        check=True,
        timeout=10,
    )
    source = live_harness.write_ass(tmp_path / "unique.ass", cues)
    source.write_text(
        source.read_text()
        .replace("Arial", alias)
        .replace("[Script Info]", f"[Script Info]\nKerning: {kerning}")
    )
    extra = (f"--osd-fonts-dir={fonts}",) if osd_access else ()
    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            (
                "--sub-ass-override=no",
                "--blend-subtitles=no",
                "--osd-level=0",
                "--geometry=1280x720",
                "--hidpi-window-scale=no",
                "--vo=gpu",
                "--gpu-sw=yes",
                "--osd-shaper=complex",
                *extra,
            ),
            media=attached,
            subtitles=source,
            coloring="whole-cue-osd",
        ),
    ) as (_tmp, session, ipc):
        poll_until(
            session,
            lambda: session.graph.cue.draw_request().whole_cue is not None,
            "attachment cue never prepared",
        )
        request = session.graph.cue.draw_request()
        assert request.boxes
        assert request.whole_cue.osd_reason == reason
        if reason == "eligible":
            colors = tuple((layer.token, 0x00FF00) for layer in request.whole_cue.layers)
            assert colors
            payload = osd_payload(request.whole_cue, colors)
            reply = ipc.command(
                "osd-overlay", 2001, "ass-events", payload, *request.whole_cue.resolution
            )
            assert reply["error"] == "success"
        assert ipc.query("sub-visibility") is True


@pytest.mark.usefixtures("enabled_telemetry")
@pytest.mark.mpv_min("0.41")
def test_first_cue_osd_is_qualified_during_initial_blank(monkeypatch):
    from test_cue_color_timeline import _coloring

    spans = record_spans(monkeypatch)
    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        cues=((2.0, 5.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            coloring="whole-cue-osd",
            start_seconds=0,
            wait_for_cue=False,
            extra_args=(
                "--sub-ass-override=no",
                "--blend-subtitles=no",
                "--geometry=1280x720",
                "--hidpi-window-scale=no",
                "--osd-shaper=complex",
            ),
        ),
    ) as (_tmp, session, ipc):
        native = session.graph.subtitle_presentation.native
        poll_until(
            session,
            lambda: native.worker.stats.prefetch_cache_entries > 0,
            "first cue was not prepared during the blank gap",
        )
        assert any(
            s["name"] == "subtitle_osd_qualification" and s["attrs"].get("comparison") == "exact"
            for s in spans
        )
        assert not session.graph.subtitle_presentation.cue.current.boxes
        spans.clear()

        ipc.command("seek", "2.1", "absolute+exact")
        poll_until(
            session,
            lambda: any(
                s["name"] == "subtitle_color_ack" and s["attrs"].get("accepted") for s in spans
            ),
            "cached first cue was not colored",
        )

        assert native.worker.stats.ready_before_presented == 1
        assert not any(s["name"] == "subtitle_geometry_libass" for s in spans)
        assert session.graph.cue.draw_request().whole_cue.osd_reason == "eligible"


@pytest.mark.parametrize("vo", ["gpu", "gpu-next"])
@pytest.mark.usefixtures("enabled_telemetry")
@pytest.mark.mpv_min("0.41")
def test_osd_lookahead_renders_hidden_and_survives_blank_gaps(tmp_path, monkeypatch, vo):
    from test_cue_color_timeline import _coloring

    spans = record_spans(monkeypatch)
    log = tmp_path / "mpv.log"
    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        cues=((1.0, 7.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "auto",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            (
                "--sub-ass-override=no",
                "--blend-subtitles=no",
                "--osd-level=0",
                "--geometry=1280x720",
                "--hidpi-window-scale=no",
                f"--vo={vo}",
                "--gpu-sw=yes",
                "--osd-shaper=complex",
                f"--log-file={log}",
                "--msg-level=osd/libass=debug",
            ),
            coloring="whole-cue-osd",
            start_seconds=0,
            wait_for_cue=False,
        ),
    ) as (tmp, session, ipc):
        poll_until(
            session,
            lambda: any(
                s["name"] == "subtitle_osd_warmup" and s["attrs"].get("color_status") == "complete"
                for s in spans
            ),
            "actual mpv OSD renderer never warmed",
        )

        def screenshot(name):
            path = tmp / name
            assert ipc.command("screenshot-to-file", str(path), "window")["error"] == "success"
            return np.asarray(Image.open(path).convert("RGB"))

        def renderer_initializations():
            return sum(
                "[osd/libass]" in line and "ASS library version" in line
                for line in log.read_text().splitlines()
            )

        empty = screenshot("hidden.png")
        initialized = renderer_initializations()
        assert initialized > 0
        paused = False
        ipc.command("set_property", "pause", paused)
        poll_until(
            session, lambda: bool(session.graph.cue.draw_request().boxes), "first cue absent"
        )
        paused = True
        ipc.command("set_property", "pause", paused)
        poll_until(
            session,
            lambda: any(
                s["name"] == "subtitle_color_ack"
                and s["attrs"].get("accepted")
                and s["attrs"].get("tokens", 0) > 0
                for s in spans
            ),
            "first color absent",
        )
        colored = screenshot("first.png")
        assert np.any(colored != empty)
        assert renderer_initializations() == initialized

        ipc.command("seek", "0", "absolute+exact")
        poll_until(session, lambda: not session.graph.cue.draw_request().text, "blank not observed")
        assert np.array_equal(screenshot("blank.png"), empty)
        spans.clear()
        ipc.command("seek", "1.1", "absolute+exact")
        poll_until(
            session,
            lambda: any(
                s["name"] == "subtitle_color_ack"
                and s["attrs"].get("accepted")
                and s["attrs"].get("tokens", 0) > 0
                for s in spans
            ),
            "second color absent",
        )
        second = screenshot("second.png")
        assert np.array_equal(second, colored)
        assert renderer_initializations() == initialized
