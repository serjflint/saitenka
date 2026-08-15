"""Assembly tests against the system libass selected for this platform."""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import libasslite

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\c&H112233&}猫{\\c&H445566&}を見る{\\c&H778899&}猫
""".encode()


def configured_library() -> Path | None:
    configured = os.environ.get("LIBASSLITE_LIBRARY")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path("/opt/homebrew/lib/libass.dylib")
    return None


def renderer(ass: bytes = ASS) -> libasslite.AssRenderer:
    return libasslite.AssRenderer(ass, library_path=configured_library())


def character_bounds(result: libasslite.AssRenderResult, rgb: int) -> tuple[int, int, int, int]:
    layers = [layer for layer in result.layers if layer.image_type == 0 and layer.color >> 8 == rgb]
    assert layers
    return (
        min(layer.dst_x for layer in layers),
        min(layer.dst_y for layer in layers),
        max(layer.dst_x + layer.width for layer in layers),
        max(layer.dst_y + layer.height for layer in layers),
    )


def geometry_signature(result: libasslite.AssRenderResult) -> list[tuple]:
    return [
        (
            layer.image_type,
            layer.width,
            layer.height,
            layer.dst_x,
            layer.dst_y,
            layer.bitmap,
        )
        for layer in result.layers
    ]


def test_loads_public_abi_and_recovers_repeated_token_bounds() -> None:
    ass_renderer = renderer()

    result = ass_renderer.render(1_500, (1280, 720), (1280, 720))

    assert ass_renderer.library_version() & 0xFFFF0000 == 0x01700000
    assert ass_renderer.library_path()
    assert {layer.image_type for layer in result.layers} >= {0, 1, 2}
    assert all(len(layer.bitmap) == layer.width * layer.height for layer in result.layers)
    first_cat = character_bounds(result, 0x332211)
    middle = character_bounds(result, 0x665544)
    second_cat = character_bounds(result, 0x998877)
    centers = [(bounds[0] + bounds[2]) / 2 for bounds in (first_cat, middle, second_cat)]
    assert centers == sorted(centers)
    assert len(set(centers)) == 3


def test_color_only_variants_preserve_plain_fixture_geometry() -> None:
    visible = renderer()
    hit_map_ass = (
        ASS.replace(b"112233", b"010101")
        .replace(b"445566", b"020202")
        .replace(b"778899", b"030303")
    )
    hit_map = renderer(hit_map_ass)

    visible_result = visible.render(1_500, (1280, 720), (1280, 720))
    hit_map_result = hit_map.render(1_500, (1280, 720), (1280, 720))

    assert geometry_signature(visible_result) == geometry_signature(hit_map_result)


def test_geometry_oracle_detects_layout_change() -> None:
    baseline = renderer()
    changed = renderer(ASS.replace(b"Arial,48", b"Arial,60"))

    baseline_result = baseline.render(1_500, (1280, 720), (1280, 720))
    changed_result = changed.render(1_500, (1280, 720), (1280, 720))

    assert geometry_signature(baseline_result) != geometry_signature(changed_result)


def test_one_renderer_serializes_cross_thread_calls() -> None:
    ass_renderer = renderer()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda timestamp: ass_renderer.render(timestamp, (1280, 720), (1280, 720)),
                [1_500] * 16,
            )
        )

    signatures = [geometry_signature(result) for result in results]
    assert signatures and all(signature == signatures[0] for signature in signatures)


def test_explicit_path_does_not_silently_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = configured_library()
    if configured is None:
        pytest.skip("test requires a configured system libass path")
    monkeypatch.setenv("LIBASSLITE_LIBRARY", str(configured))

    with pytest.raises(RuntimeError, match="could not load libass"):
        libasslite.AssRenderer(ASS, library_path=configured.with_name("missing-libass"))


def test_invalid_geometry_is_rejected_before_native_render() -> None:
    ass_renderer = renderer()

    with pytest.raises(ValueError, match="frame_size must be positive"):
        ass_renderer.render(1_500, (0, 720), (1280, 720))

    with pytest.raises(ValueError, match="storage_size must be positive"):
        ass_renderer.render(1_500, (1280, 720), (1280, -1))


@pytest.mark.parametrize("pixel_aspect", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_pixel_aspect_is_rejected_before_native_render(pixel_aspect: float) -> None:
    ass_renderer = renderer()

    with pytest.raises(ValueError, match="pixel_aspect must be finite and positive"):
        ass_renderer.render(
            1_500,
            (1280, 720),
            (960, 720),
            pixel_aspect=pixel_aspect,
        )


def test_explicit_pixel_aspect_changes_only_horizontal_geometry() -> None:
    square = renderer()
    wide = renderer()

    square_bounds = character_bounds(
        square.render(1_500, (1280, 720), (960, 720), pixel_aspect=1.0),
        0x332211,
    )
    wide_bounds = character_bounds(
        wide.render(1_500, (1280, 720), (960, 720), pixel_aspect=4 / 3),
        0x332211,
    )

    assert wide_bounds[0::2] != square_bounds[0::2]
    assert wide_bounds[1::2] == square_bounds[1::2]


def test_close_is_idempotent_and_blocks_render() -> None:
    ass_renderer = renderer()

    ass_renderer.close()
    ass_renderer.close()

    with pytest.raises(RuntimeError, match="closed"):
        ass_renderer.render(1_500, (1280, 720), (1280, 720))
