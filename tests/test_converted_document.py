"""The reconstruction of mpv's converted-track document, against mpv's own arithmetic."""

from __future__ import annotations

import pytest
from util import requires_libass

from saitenka.subtitles import converted
from saitenka.subtitles.geometry import RendererState

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


def test_the_style_row_states_alpha_the_way_a_style_row_states_it() -> None:
    """The struct and the text are not the same four bytes. `MP_ASS_RGBA` packs `RRGGBBAA` for the
    field mpv assigns; a `Style:` row is `&HAABBGGRR`, which libass reorders on parse. Printing
    mpv's integer verbatim reads the alpha off the wrong end, and opaque white becomes `&HFFFFFF00`
    — alpha `0xFF`, fully transparent, so libass paints nothing and every token loses its box.
    """
    fields = (
        converted.style_row(converted.SubStyle(), converted.PLAYRES_Y, HD, scale=1.0)
        .removeprefix("Style: ")
        .split(",")
    )

    assert fields[3] == "&H00FFFFFF"  # PrimaryColour: opaque white
    assert fields[6] == "&H50000000"  # BackColour: black at mpv's default opacity


#: One real `mpv.conf`, as mpv reports it back through `options/…` — colors normalised to
#: `#AARRGGBB`, choices by name, sizes typed. Every value here differs from mpv's default, which is
#: the point: a reader that dropped any of them would still pass a test built from the defaults.
REPORTED = {
    "sub-font": "Symbola",
    "sub-font-size": 44.0,
    "sub-color": "#FFECEFF4",
    "sub-outline-color": "#FF2E3440",
    "sub-back-color": "#FF2E3440",
    "sub-border-style": "background-box",
    "sub-outline-size": 1.15,
    "sub-shadow-offset": 2.0,
    "sub-spacing": 1.5,
    "sub-margin-x": 40,
    "sub-margin-y": 60,
    "sub-align-x": "left",
    "sub-align-y": "top",
    "sub-blur": 0.5,
    "sub-bold": True,
    "sub-italic": True,
    "sub-justify": "right",
}


def test_every_style_option_mpv_reports_reaches_the_style() -> None:
    """A converted cue mpv draws under the user's `--sub-*` options and we measure under mpv's
    defaults is a cue whose boxes sit where somebody else's subtitles would be. This is the
    whole set, in the shapes `options/…` actually reports — choices by name, colors `#AARRGGBB`.
    """
    style = converted.style_of(REPORTED)

    assert style == converted.SubStyle(
        font="Symbola",
        font_size=44.0,
        color=converted.Color(0xEC, 0xEF, 0xF4, 0xFF),
        outline_color=converted.Color(0x2E, 0x34, 0x40, 0xFF),
        back_color=converted.Color(0x2E, 0x34, 0x40, 0xFF),
        border_style=4,
        outline_size=1.15,
        shadow_offset=2.0,
        spacing=1.5,
        margin_x=40,
        margin_y=60,
        align_x=-1,
        align_y=-1,
        blur=0.5,
        bold=True,
        italic=True,
        justify=3,
    )


def test_no_style_option_is_left_at_a_default_the_reader_cannot_reach() -> None:
    """The completeness half: reading sixteen of seventeen fields is the failure mode this exists
    to catch, because the seventeenth is silently mpv's default and nothing says so."""
    default = converted.SubStyle()
    read = converted.style_of(REPORTED)

    unread = [name for name in default.__slots__ if getattr(read, name) == getattr(default, name)]

    assert unread == ["margin_y_offset"]  # not an option: mpv passes 0 for subtitles


def test_a_reported_value_in_a_shape_the_reader_does_not_know_keeps_the_default() -> None:
    """mpv renames these — `--sub-border-size` became `--sub-outline-size` — so an unfamiliar value
    is a version difference, not a broken player. Refusing the track would cost an episode's
    interaction over one field; measuring the rest against a default that is wrong for one field is
    the smaller error, and it is logged."""
    style = converted.style_of({"sub-margin-y": "not-a-number", "sub-align-x": "sideways"})

    assert style == converted.SubStyle()


CUE, SPANS = "（鳥のさえずり）", ((1, 2), (2, 3), (3, 7))
ROW = f"Dialogue: 0,0:00:11.25,0:00:13.00,Default,,0,0,0,,{CUE}"


def _render_cue(backend: object, style: converted.SubStyle | None = None) -> list:
    """The tokens real libass paints for a converted cue laid out under `style`."""
    from saitenka.subtitles import (
        GeometryPaletteEntry,
        GeometryRequest,
        SubtitleTrackId,
        TokenAnnotation,
    )
    from saitenka.subtitles.ass_geometry import prepare_ass_hit_map_frame

    frame = (HD.width, HD.height)
    scale = converted.font_scale(HD)
    track = SubtitleTrackId("converted-render")
    prepared = prepare_ass_hit_map_frame(
        converted.document(ROW, HD, style=style, scale=scale),
        track,
        active_rows=ROW,
        text=CUE,
        tokens=[TokenAnnotation(index, *span) for index, span in enumerate(SPANS)],
    )
    snapshot = backend.render(  # type: ignore[attr-defined]
        GeometryRequest(
            1,
            track,
            prepared.frame_id,
            11_256,
            frame,
            frame,
            prepared.ass,
            palette=tuple(
                GeometryPaletteEntry(
                    entry.event_id, entry.token_index, entry.rgb, entry.font_name, 44.0
                )
                for entry in prepared.palette
            ),
            reserved_rgb=prepared.reserved_rgb,
            renderer_state=RendererState(font_scale=scale),
        )
    )
    return list(snapshot.tokens)


def _union(tokens: list) -> tuple[int, int, int, int]:
    boxes = [token.bounds for token in tokens]
    return (
        min(box.x for box in boxes),
        min(box.y for box in boxes),
        max(box.x + box.width for box in boxes),
        max(box.y + box.height for box in boxes),
    )


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_every_token_of_a_converted_cue_gets_a_box_from_real_libass() -> None:
    """The reconstruction's arithmetic being right is not the claim that matters. The claim is that
    libass, handed this document, paints each token in the color it was asked for — and only a real
    render can say so. Everything above compares numbers to numbers, which is how a style row that
    made every glyph fully transparent passed eighteen tests and failed every cue on a user's
    screen.
    """
    requires_libass()
    from saitenka.subtitles.libass_backend import LibassGeometryBackend

    backend = LibassGeometryBackend()
    try:
        tokens = _render_cue(backend)
    finally:
        backend.close()

    assert [token.token_index for token in tokens] == [0, 1, 2]
    assert all(token.bounds.width > 0 and token.bounds.height > 0 for token in tokens)


#: Fields of `SubStyle` a user sets through a `--sub-*` option, each of which moves or resizes the
#: cue on screen. If the style reaching `document()` is not the one mpv applied, every box drawn
#: under one of these lands beside its word — which is what a converted track did on every machine
#: while `document()` was called without a `style=` at all.
MOVING_STYLE_FIELDS = (
    ("font_size", converted.SubStyle(font_size=60.0)),
    ("margin_y", converted.SubStyle(margin_y=120)),
    ("spacing", converted.SubStyle(spacing=6.0)),
    ("bold", converted.SubStyle(bold=True)),
    ("align_y", converted.SubStyle(align_y=-1)),
    ("margin_x", converted.SubStyle(margin_x=200, align_x=-1)),
)


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_a_style_the_user_changed_moves_the_boxes_libass_paints() -> None:
    """The oracle the style port never had: not "the row prints the number" but "libass lays the cue
    out somewhere else because of it". A `Style:` field the row drops, mis-scales, or writes into
    the wrong column reads green against arithmetic and silently pins every box to mpv's defaults.
    """
    requires_libass()
    from saitenka.subtitles.libass_backend import LibassGeometryBackend

    backend = LibassGeometryBackend()
    try:
        baseline = _union(_render_cue(backend))
        moved = {name: _union(_render_cue(backend, style)) for name, style in MOVING_STYLE_FIELDS}
    finally:
        backend.close()

    assert [name for name, union in moved.items() if union == baseline] == []


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_a_reused_renderer_measures_each_cue_against_its_own_document() -> None:
    """The differential that makes renderer reuse safe to have.

    A renderer now survives across cues and has its track swapped, which is the whole saving — and
    also the one way it could go silently wrong: a renderer still holding the previous cue's
    document answers with that cue's boxes, and hit regions land beside the words with every meter
    green. So each cue is measured twice, once through a shared renderer and once through a fresh
    one, and the two must agree exactly.
    """
    requires_libass()
    from saitenka.subtitles.libass_backend import LibassGeometryBackend

    cues = ["（鳥のさえずり）", "うんうん…。", "キーボードを打つ音"]

    from saitenka.subtitles import (
        GeometryPaletteEntry,
        GeometryRequest,
        SubtitleTrackId,
        TokenAnnotation,
    )
    from saitenka.subtitles.ass_geometry import prepare_ass_hit_map_frame

    frame = (HD.width, HD.height)

    def boxes(backend, cue: str) -> list[tuple[int, int, int, int]]:
        row = f"Dialogue: 0,0:00:11.25,0:00:13.00,Default,,0,0,0,,{cue}"
        track = SubtitleTrackId("swap")
        prepared = prepare_ass_hit_map_frame(
            converted.document(row, HD, scale=1.0),
            track,
            active_rows=row,
            text=cue,
            tokens=[TokenAnnotation(0, 0, 2), TokenAnnotation(1, 2, 4)],
        )
        snapshot = backend.render(
            GeometryRequest(
                1,
                track,
                prepared.frame_id,
                11_256,
                frame,
                frame,
                prepared.ass,
                palette=tuple(
                    GeometryPaletteEntry(
                        entry.event_id, entry.token_index, entry.rgb, entry.font_name, 44.0
                    )
                    for entry in prepared.palette
                ),
                reserved_rgb=prepared.reserved_rgb,
                renderer_state=RendererState(font_scale=1.0),
            )
        )
        return [(t.bounds.x, t.bounds.y, t.bounds.width, t.bounds.height) for t in snapshot.tokens]

    shared = LibassGeometryBackend()
    try:
        # Interleaved and repeated: a swap that only ever moves forward would hide a renderer that
        # kept state from two cues ago.
        reused = {cue: boxes(shared, cue) for cue in cues + cues[::-1]}
    finally:
        shared.close()

    for cue in cues:
        fresh = LibassGeometryBackend()
        try:
            assert reused[cue] == boxes(fresh, cue), f"{cue} was measured against another document"
        finally:
            fresh.close()


#: A two-line cue with a short second line. `--sub-justify` decides where that second line starts,
#: so it is the only shape in which the option is visible at all.
WRAPPED = "ながいいちぎょうめです\nみじかいに"
WRAPPED_ROW = (
    "Dialogue: 0,0:00:11.25,0:00:13.00,Default,,0,0,0,,ながいいちぎょうめです\\Nみじかいに"
)


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_justify_reaches_libass_even_though_a_style_row_cannot_state_it() -> None:
    """`--sub-justify` is sent on every run and a V4+ `Format:` line has no `Justify` field — mpv
    writes it onto the style struct directly. The only seam that carries it is a selective style
    override on the renderer, and its bit is not where counting the names would put it: one below
    JUSTIFY is `ASS_OVERRIDE_FULL_STYLE`, which replaces every field of every style instead.
    """
    from saitenka.subtitles import (
        GeometryPaletteEntry,
        GeometryRequest,
        SubtitleTrackId,
        TokenAnnotation,
    )
    from saitenka.subtitles.ass_geometry import prepare_ass_hit_map_frame

    requires_libass()
    from saitenka.subtitles.libass_backend import LibassGeometryBackend

    frame = (HD.width, HD.height)
    track = SubtitleTrackId("converted-justify")
    prepared = prepare_ass_hit_map_frame(
        converted.document(WRAPPED_ROW, HD, scale=1.0),
        track,
        active_rows=WRAPPED_ROW,
        text=WRAPPED,
        tokens=[TokenAnnotation(index, *span) for index, span in enumerate(((0, 5), (12, 17)))],
    )

    def second_line_x(justify: int) -> int:
        snapshot = backend.render(
            GeometryRequest(
                1,
                track,
                prepared.frame_id,
                11_256,
                frame,
                frame,
                prepared.ass,
                palette=tuple(
                    GeometryPaletteEntry(
                        entry.event_id, entry.token_index, entry.rgb, entry.font_name, 44.0
                    )
                    for entry in prepared.palette
                ),
                reserved_rgb=prepared.reserved_rgb,
                renderer_state=RendererState(font_scale=1.0, justify=justify),
            )
        )
        return next(token.bounds.x for token in snapshot.tokens if token.token_index == 1)

    backend = LibassGeometryBackend()
    try:
        left, centre, right = (second_line_x(justify) for justify in (1, 2, 3))
    finally:
        backend.close()

    assert left < centre < right
