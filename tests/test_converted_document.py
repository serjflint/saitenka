"""The reconstruction of mpv's converted-track document, against mpv's own arithmetic."""

from __future__ import annotations

import pytest

from saitenka.subtitles import converted

EVENTS = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello world"
HD = converted.RenderSpace(1920, 1080)


def test_a_square_frame_leaves_the_font_scale_alone() -> None:
    """`--sub-scale-with-window` divides the frame height by the height the text scaled with; with
    no letterboxing those are the same number."""
    assert converted.font_scale(HD) == pytest.approx(1.0)


def test_a_letterboxed_frame_shrinks_the_text_by_the_bars() -> None:
    """The case the plan flags: a 2.39:1 file in a 16:9 window. Assuming a scale of 1 here lays
    every box out around 30% too small, uniformly, with every existing meter reading green."""
    bar = (1080 - round(1920 / 2.39)) // 2
    letterboxed = converted.RenderSpace(1920, 1080, (bar, bar, 0, 0))

    scale = converted.font_scale(letterboxed, use_margins=False)

    assert scale == pytest.approx(1080 / (1080 - 2 * bar), rel=1e-6)
    assert scale > 1.3


def test_margins_change_which_height_the_text_scales_with() -> None:
    """`get_libass_scale_height`: without margins the text scales with the video's visible height,
    with them, with the height it would have resized to fit the frame.

    Bars on both axes is the only shape where the two answers differ — a window whose aspect matches
    neither the video's nor the frame's. Letterbox alone and pillarbox alone each return the same
    number down both branches, so a test built on one of those passes with the branch deleted.
    """
    boxed = converted.RenderSpace(1920, 1080, (100, 100, 240, 240))

    assert converted.libass_scale_height(boxed, use_margins=False) == 880
    # 1920/1440 * 880 overshoots the frame, so the fit clamps to it — a 23% larger font either way.
    assert converted.libass_scale_height(boxed, use_margins=True) == 1080
    assert converted.font_scale(boxed, use_margins=True) != converted.font_scale(
        boxed, use_margins=False
    )


@pytest.mark.parametrize(
    ("margins", "height"),
    [
        ((135, 135, 0, 0), 810),  # letterbox: the fitted height is the visible height
        ((0, 0, 240, 240), 1080),  # pillarbox: the fit clamps to the frame
    ],
)
def test_a_single_pair_of_bars_scales_the_same_either_way(
    margins: tuple[int, int, int, int], height: float
) -> None:
    """Boundary cases, named for what they are. Neither discriminates the branch; both pin the
    arithmetic that produces the same answer down each side."""
    space = converted.RenderSpace(1920, 1080, margins)

    assert converted.libass_scale_height(space, use_margins=False) == height
    assert converted.libass_scale_height(space, use_margins=True) == height


def test_scale_by_window_off_normalises_to_a_720p_reference() -> None:
    assert converted.font_scale(HD, scale_by_window=False) == pytest.approx(720 / 1080)


def test_playres_x_follows_the_display_aspect_and_the_height_does_not() -> None:
    """libavcodec converts at a fixed 384x288 (4:3); mpv rewrites the width from the DAR because
    since libass f08f8ea5 `PlayResX` sets border and shadow widths."""
    assert converted.play_res_x(HD) == round(288 * 16 / 9)
    assert converted.play_res_x(converted.RenderSpace(1440, 1080)) == 384


def test_the_document_carries_libavcodecs_header_and_mpvs_style() -> None:
    text = converted.document(EVENTS, HD).decode()

    assert "PlayResY: 288" in text
    assert f"PlayResX: {converted.play_res_x(HD)}" in text
    assert "ScaledBorderAndShadow: yes" in text
    assert "YCbCr Matrix: None" in text
    assert text.rstrip().endswith(EVENTS)


def test_the_style_carries_mpvs_defaults_translated_to_the_track_resolution() -> None:
    """`mp_ass_set_style` scales every size from a reference `PlayResY` of 720 to the track's 288."""
    row = converted.style_row(converted.SubStyle(), converted.PLAYRES_Y, HD, scale=1.0)
    fields = row.removeprefix("Style: ").split(",")

    assert fields[0:2] == ["Default", "sans-serif"]
    assert float(fields[2]) == pytest.approx(38 * 288 / 720)
    assert float(fields[16]) == pytest.approx(1.65 * 288 / 720)  # Outline


def test_alpha_is_inverted_because_ass_stores_transparency() -> None:
    """`MP_ASS_RGBA` writes `0xFF - a`: mpv states opacity and ASS stores its complement. Getting
    this backwards makes a fully opaque color fully transparent."""
    assert converted.Color(255, 255, 255, 255).as_ass() == 0xFFFFFF00
    assert converted.Color(0, 0, 0, 175).as_ass() == 0x00000050


def test_every_style_is_swept_to_the_neutral_base_direction() -> None:
    """`Encoding = -1` is libass's neutral base direction, which mpv sets on a converted track
    unless `--sub-vsfilter-bidi-compat` is on. It also makes libass lay the whole event out as one
    run, so it is a layout decision rather than a bidi footnote."""
    row = converted.style_row(converted.SubStyle(), converted.PLAYRES_Y, HD, scale=1.0)

    assert row.split(",")[-1] == "-1"


@pytest.mark.parametrize(
    ("align_x", "align_y", "expected"),
    # `1 + (align_x + 1) + (align_y + 2) % 3 * 4` — mpv's own expression, checked at its corners.
    [(-1, 1, 1), (0, 1, 2), (1, 1, 3), (0, -1, 6), (0, 0, 10)],
)
def test_alignment_is_mpvs_expression(align_x: int, align_y: int, expected: int) -> None:
    style = converted.SubStyle(align_x=align_x, align_y=align_y)
    row = converted.style_row(style, converted.PLAYRES_Y, HD, scale=1.0)

    assert int(row.removeprefix("Style: ").split(",")[18]) == expected


def test_the_vertical_margin_scales_with_the_font_and_the_horizontal_with_playres() -> None:
    """mpv's asymmetry, not a transcription slip: `MarginL`/`R` are rescaled by how far `PlayResX`
    moved, `MarginV` by the font scale (`sd_ass.c:630-635`)."""
    row = converted.style_row(converted.SubStyle(), converted.PLAYRES_Y, HD, scale=2.0)
    fields = row.removeprefix("Style: ").split(",")
    reference = 288 / 720

    assert int(fields[19]) == round(round(19 * reference) * converted.play_res_x(HD) / 384)
    assert int(fields[21]) == round(round(34 * reference) * 2.0)
