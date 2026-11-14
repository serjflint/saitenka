"""Whole-event raster equivalence and optional modes through the real presentation pipeline."""

from __future__ import annotations

import os

import numpy as np
import pytest
from live_harness import LayoutLiveOptions, live_reader, poll_until
from PIL import Image, ImageFilter
from saitenka_subtitles.whole_cue import compose, osd_payload
from util import record_spans

from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(30),
    pytest.mark.skipif(
        not os.environ.get("SAITENKA_LIVE"), reason="requires live font/display environment"
    ),
]


@pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)
def test_converted_letterboxed_colors_cover_the_native_subtitles(tmp_path):
    from live_harness import make_clip_and_sub
    from test_cue_color_timeline import _coloring

    from saitenka.app.overlay_ids import OverlayId
    from saitenka.runtime import SurfaceStatus

    clip, source = make_clip_and_sub(tmp_path, ((0.0, 8.0, "猫を見る"), (0.0, 8.0, "犬も見る")))
    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        layout=LayoutLiveOptions(
            media=clip,
            subtitles=source,
            coloring="whole-cue-overpaint",
            extra_args=(
                "--sub-use-margins=yes",
                "--sub-ass-force-margins=no",
                "--sub-margin-y=60",
                "--blend-subtitles=no",
                "--osd-level=0",
                "--geometry=1280x800",
                "--hidpi-window-scale=no",
                "--vo=gpu",
                "--gpu-sw=yes",
            ),
        ),
    ) as (tmp, session, ipc):
        surfaces = session.graph.lifecycle_surfaces
        poll_until(
            session,
            lambda: (
                (surface := surfaces.snapshot(OverlayId.OVERPAINT)) is not None
                and surface.status is SurfaceStatus.PRESENT
            ),
            "converted cue colors were not acknowledged",
        )
        dimensions = ipc.query("osd-dimensions")
        assert dimensions["mb"] > 20
        path = tmp / "colored.png"
        assert ipc.command("screenshot-to-file", str(path), "window")["error"] == "success"
        colored = np.asarray(Image.open(path).convert("RGB"))
        session.graph.subtitle_presentation.clear_pixels()
        poll_until(
            session,
            lambda: surfaces.snapshot(OverlayId.OVERPAINT).status is SurfaceStatus.ABSENT,
            "converted cue colors were not cleared",
        )
        path = tmp / "native.png"
        assert ipc.command("screenshot-to-file", str(path), "window")["error"] == "success"
        native = np.asarray(Image.open(path).convert("RGB"))

        changed = np.max(np.abs(colored.astype(int) - native.astype(int)), axis=2) > 20
        ink = np.all(native > 100, axis=2)
        support = np.asarray(Image.fromarray(ink).filter(ImageFilter.MaxFilter(7)))
        assert np.count_nonzero(changed) > 100
        assert np.count_nonzero(changed & ~support) / np.count_nonzero(changed) < 0.02


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
    ("mode", "device", "override"),
    [
        pytest.param("whole-cue-auto", "overprint", "no", marks=pytest.mark.mpv_min("0.41")),
        pytest.param("whole-cue-auto", "overpaint", "no", marks=pytest.mark.mpv_min("0.41")),
        pytest.param(
            "whole-cue-overpaint",
            "overpaint",
            "no",
            marks=pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN),
        ),
        pytest.param("whole-cue-osd", "overprint", "no", marks=pytest.mark.mpv_min("0.41")),
        # mpv's own default, which `run` no longer overrides.
        pytest.param("whole-cue-osd", "overprint", "scale", marks=pytest.mark.mpv_min("0.41")),
        pytest.param(
            "boxes-only", "none", "no", marks=pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN)
        ),
    ],
)
@pytest.mark.usefixtures("enabled_telemetry")
def test_optional_coloring_modes_upload_or_keep_boxes(mode, device, override, monkeypatch):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords
    from test_live_colored_track import _selected

    from saitenka.app import colored_subs
    from saitenka.app.scoring import Coloring, Palette

    spans = record_spans(monkeypatch)
    shaper = "complex" if device == "overprint" else "simple"
    with live_reader(
        native_visible=True,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])), Palette()),
        cues=((0.5, 8.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            (
                f"--sub-ass-override={override}",
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
            start_seconds=0,
            wait_for_cue=False,
        ),
    ) as (tmp, session, ipc):
        if mode == "whole-cue-auto":
            poll_until(
                session,
                lambda: colored_subs.is_copy(_selected(ipc)),
                "automatic coloring did not select its copy",
            )
            original = colored_subs.origin_track(ipc.query("track-list"), _selected(ipc))
            assert original is not None
            # Declining the copy exercises automatic overlay fallback through the player.
            ipc.command("set_property", "sid", original["id"])
            poll_until(
                session,
                lambda: not any(colored_subs.is_copy(track) for track in ipc.query("track-list")),
                "declined copy remained loaded",
            )
        ipc.command("seek", "0.6", "absolute+exact")
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
            lambda: (
                (request := session.graph.cue.draw_request()).whole_cue is not None
                and bool(request.boxes)
            ),
            "attachment cue never acquired prepared scan geometry",
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
        rendered = [s for s in spans if s["name"] == "subtitle_geometry_libass"]
        assert not rendered, rendered
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
