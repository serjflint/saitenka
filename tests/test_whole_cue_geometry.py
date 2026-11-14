"""Subtitle placement against independent libass renders, without a player or display."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from saitenka_subtitles import (
    GeometryRequest,
    SubtitleTrackId,
    TokenAnnotation,
    authored_ass_rows_at,
    prepare_ass_hit_map_frame,
)
from saitenka_subtitles.geometry import FontProvider, FontSetup, PaintQualification
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_subtitles.whole_cue import WholeCue, compose, osd_payload, osd_template
from test_ass_geometry import ASS
from test_whole_cue import prepared_source
from util import requires_libass

_FONT = str(Path(__file__).parents[1] / "src/saitenka/assets/fonts/NotoSansJP.ttf")


def _character_masks():
    path = Path(__file__).resolve().parents[1] / "tools/character_masks.py"
    spec = importlib.util.spec_from_file_location("character_masks", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("frame", "margins"),
    [
        ((1280, 720), (0, 0, 0, 0)),
        ((3024, 1898), (98, 99, 0, 0)),
        ((3456, 2168), (112, 112, 0, 0)),
    ],
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
    requires_libass()
    source = ASS.decode().replace("猫を見る", raw).replace("Arial", "Noto Sans JP Thin").encode()
    fonts = FontSetup(
        default_font=_FONT,
        default_family="Noto Sans JP Thin",
        font_provider=FontProvider.NONE,
    )
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
        font_setup=fonts,
        osd_font_setup=fonts,
    )
    backend = LibassGeometryBackend()
    try:
        snapshot = backend.render(request)
    finally:
        backend.close()
    assert snapshot.whole_cue.osd_reason == "eligible"
    assert dict(snapshot.whole_cue.evidence)["comparison"] == "exact"


def test_occluded_lower_event_keeps_scanning_without_coloring():
    requires_libass()
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
        font_setup=FontSetup(default_font=_FONT, font_provider=FontProvider.NONE),
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
    requires_libass()
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
        renderer = libasslite.AssRenderer(
            document, default_font=_FONT, font_provider=FontProvider.NONE
        )
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
    ("text", "boundary", "font_name"),
    [
        ("猫を見る", 2, "NotoSansJP.ttf"),
        ("office AVATAR", 6, "NotoSans.ttf"),
        ("a\u0308bc", 2, "NotoSans.ttf"),
    ],
)
@pytest.mark.parametrize("selected", [(0,), (0, 1)])
@pytest.mark.parametrize(
    ("frame", "margins"),
    [((1280, 720), (0, 0, 0, 0)), ((3456, 2168), (112, 112, 0, 0))],
)
def test_marker_layers_preserve_opaque_color_and_final_event_geometry(
    text, boundary, font_name, selected, frame, margins
):
    requires_libass()
    from fontTools.ttLib import TTFont

    libasslite = pytest.importorskip("libasslite")
    font_path = str(Path(_FONT).with_name(font_name))
    with TTFont(font_path) as face:
        assert all(ord(character) in face.getBestCmap() for character in text)

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
        frame,
        (1280, 720),
        prepared.ass,
        margins=margins,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
        native_ass=source,
        coloring="whole-cue-overpaint",
        whole_cue=WholeCue(),
        font_setup=FontSetup(default_font=font_path, font_provider=FontProvider.NONE),
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
    renderer = libasslite.AssRenderer(
        direct, default_font=font_path, font_provider=FontProvider.NONE
    )
    try:
        reference = renderer.render(1500, frame, (1280, 720), margins=margins)
    finally:
        renderer.close()
    expected_alpha = np.zeros((frame[1], frame[0]), dtype=np.uint8)
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
    oracle = _character_masks()
    result = oracle.compare_mask(expected_alpha, observed_alpha, expected_alpha)
    assert result["verdict"] == "passed", result
    displaced = np.roll(observed_alpha, shift=(-6, 4), axis=(0, 1))
    assert oracle.compare_mask(expected_alpha, displaced, expected_alpha)["verdict"] == "failed"
