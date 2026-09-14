from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from saitenka_subtitles.ass_geometry import prepare_ass_hit_map_frame
from saitenka_subtitles.document import (
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
    TokenAnnotation,
)
from saitenka_subtitles.fractional import PhaseMask, match_phases
from saitenka_subtitles.fragments import probe_document
from saitenka_subtitles.geometry import (
    FontProvider,
    FontSetup,
    GeometryPaletteEntry,
    GeometryRequest,
)
from saitenka_subtitles.libass_backend import LibassGeometryBackend
from saitenka_subtitles.overprint import TokenPaint, event_lines


@given(dx=st.integers(-32, 32), dy=st.integers(-32, 32), alpha=st.integers(1, 255))
def test_matching_preserves_fractional_anchor_and_disconnected_mark(dx, dy, alpha):
    coverage = bytes((alpha, 0, 0, 0, 0, alpha))
    phase = PhaseMask(2, 3, coverage, dx / 8, dy / 8)

    assert match_phases(coverage, 2, 3, [(phase,)]) == ((-dx / 8, -dy / 8),)


@pytest.mark.parametrize("coverage", [b"\xff\0\0\0\0\0", b"\xff\0\0\0\xff\xff"])
def test_matching_refuses_missing_or_extra_mark(coverage):
    phase = PhaseMask(2, 3, b"\xff\0\0\0\0\xff", 0.375, 0.125)

    assert match_phases(coverage, 2, 3, [(phase,)]) is None


def test_matching_selects_native_phase_instead_of_first_same_sized_glyph():
    wrong = PhaseMask(2, 1, b"\x7f\xff", 0, 0)
    right = replace(wrong, coverage=b"\xff\x7f", dx=0.375)

    assert match_phases(b"\xff\x7f", 2, 1, [(wrong, right)]) == ((-0.375, 0),)


def test_matching_refuses_unclaimed_ink_after_last_glyph():
    phase = PhaseMask(1, 1, b"\xff", 0, 0)

    assert match_phases(b"\xff\0\xff", 3, 1, [(phase,)]) is None


def test_matching_recovers_separate_fractional_positions_in_one_token():
    first = PhaseMask(1, 2, b"\xff\x80", 0.375, 0.125)
    second = PhaseMask(1, 2, b"\x80\xff", -0.625, -0.875)

    assert match_phases(b"\xff\0\x80\x80\0\xff", 3, 2, [(first,), (second,)]) == (
        (-0.375, -0.125),
        (2.625, 0.875),
    )


def test_fractional_positions_survive_payload_serialization():
    paint = TokenPaint(
        "だ", 2460, 1200, "Noto Sans JP", 48, 0x00FF00, glyph_dx=(-1.625,), glyph_dy=(-0.125,)
    )

    assert r"\pos(2458.375,1199.875)" in event_lines(paint)[0]


def native_request(origin=(64, 40), *, spacing=4):
    frame = (960, 540)
    header = probe_document((), (), frame).document
    text = "ださい"
    body = f"{{\\an7\\pos({origin[0]},{origin[1]})\\fnNoto Sans JP Thin\\fs48\\fsp{spacing}}}{text}"
    document = header + f"Dialogue: 0,0:00:00.00,0:00:02.00,P,,0,0,0,,{body}\n"
    track = SubtitleTrackId("fractional")
    event = SubtitleEventId(track, 0, 2000, 0, 0)
    fonts = (
        (
            "NotoSansJP.ttf",
            (Path(__file__).parents[1] / "src/saitenka/assets/fonts/NotoSansJP.ttf").read_bytes(),
        ),
    )
    return GeometryRequest(
        0,
        track,
        SubtitleFrameId(track, (event,)),
        1000,
        frame,
        frame,
        document.encode(),
        palette=(
            GeometryPaletteEntry(
                event, 0, 0xFFFFFF, "Noto Sans JP Thin", 48, text, spacing=spacing
            ),
        ),
        font_setup=FontSetup(font_provider=FontProvider.NONE),
        attachments=fonts,
    )


@pytest.mark.parametrize("origin", [(64, 40), (64.375, 40.875), (65.625, 41.125)])
@pytest.mark.parametrize("spacing", [0, -0.5, 4])
def test_fractional_redraw_matches_same_renderer_native_coverage(origin, spacing):
    request = replace(native_request(origin, spacing=spacing), keep_coverage=True)
    backend = LibassGeometryBackend()
    try:
        token = backend.render(request).tokens[0]
        warm = backend.render(request).tokens[0]
    finally:
        backend.close()
    assert token.overprint_safe
    assert warm == token
    paint = TokenPaint(
        request.palette[0].text,
        token.bounds.x - token.anchor_dx,
        token.bounds.y - token.anchor_dy,
        token.font_name,
        token.font_size,
        0xFFFFFF,
        spacing=spacing,
        glyph_dx=token.glyph_dx,
        glyph_dy=token.glyph_dy,
    )
    redraw = probe_document((), (), request.frame_size).document + "\n".join(
        f"Dialogue: 0,0:00:00.00,0:00:02.00,P,,0,0,0,,{line}" for line in event_lines(paint)
    )
    renderer = LibassGeometryBackend()
    try:
        actual = renderer.render(
            replace(
                request,
                ass=redraw.encode(),
                keep_coverage=True,
                palette=(GeometryPaletteEntry(request.palette[0].event_id, 0, 0xFFFFFF),),
            )
        ).tokens[0]
    finally:
        renderer.close()
    assert (actual.bounds, actual.coverage) == (token.bounds, token.coverage)


def test_warm_glyph_atlas_does_not_reuse_another_native_phase():
    before = native_request()
    after = native_request((64.375, 40.875))
    warm_backend, cold_backend = LibassGeometryBackend(), LibassGeometryBackend()
    try:
        warm_backend.render(before)
        warm = warm_backend.render(after)
        cold = cold_backend.render(after)
    finally:
        warm_backend.close()
        cold_backend.close()

    assert warm == cold
    assert warm.tokens[0].overprint_safe


def test_phase_probe_budget_failure_retains_native_mask(monkeypatch):
    monkeypatch.setattr("saitenka_subtitles.geometry.MAX_FRAME_PIXELS", 600_000)
    backend = LibassGeometryBackend()
    try:
        token = backend.render(native_request()).tokens[0]
    finally:
        backend.close()

    assert not token.overprint_safe
    assert len(token.coverage) == token.bounds.width * token.bounds.height


def test_missing_native_runtime_is_not_a_successful_mask_fallback(tmp_path):
    backend = LibassGeometryBackend(library_path=tmp_path / "missing-libass")
    try:
        with pytest.raises(RuntimeError, match="could not load libass"):
            backend.render(native_request())
    finally:
        backend.close()


@pytest.mark.parametrize("origin", [(640, 710), (640.375, 710.875)])
def test_token_masks_reconstruct_unmodified_native_ink_across_color_run_splits(origin):
    import numpy as np

    source = native_request(origin, spacing=0)
    document = source.ass.replace(b"\\an7", b"\\an2").replace(b"\\fs48", b"\\fs40")
    document = document.replace("ださい".encode(), "あだあ".encode())
    inputs = replace(source, ass=document, frame_size=(1280, 720), storage_size=(1280, 720))
    # Script resolution and device resolution must agree for this native-reference comparison.
    document = document.replace(b"PlayResX: 960", b"PlayResX: 1280").replace(
        b"PlayResY: 540", b"PlayResY: 720"
    )
    prepared = prepare_ass_hit_map_frame(
        document,
        inputs.track_id,
        active_rows=next(
            line for line in document.decode().splitlines() if line.startswith("Dialogue:")
        ),
        text="あだあ",
        tokens=(TokenAnnotation(0, 0, 1), TokenAnnotation(1, 1, 3)),
    )
    inputs = replace(
        inputs,
        ass=prepared.ass,
        native_ass=document,
        frame_id=prepared.frame_id,
        palette=prepared.palette,
        reserved_rgb=prepared.reserved_rgb,
    )
    backend = LibassGeometryBackend()
    reference = LibassGeometryBackend()
    try:
        actual = backend.render(inputs)
        warm = backend.render(inputs)
        expected = reference.render(
            replace(
                inputs,
                ass=document,
                native_ass=b"",
                keep_coverage=True,
                palette=(GeometryPaletteEntry(prepared.palette[0].event_id, 0, 0xFFFFFF),),
                reserved_rgb=(),
            )
        )
    finally:
        backend.close()
        reference.close()
    native = expected.tokens[0]
    restored = np.zeros((native.bounds.height, native.bounds.width), dtype=np.uint8)
    for token in actual.tokens:
        x, y = token.bounds.x - native.bounds.x, token.bounds.y - native.bounds.y
        restored[y : y + token.bounds.height, x : x + token.bounds.width] = np.frombuffer(
            token.coverage, dtype=np.uint8
        ).reshape(token.bounds.height, token.bounds.width)
    assert restored.tobytes() == native.coverage
    assert warm == actual
