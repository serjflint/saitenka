r"""Device 3: the rung of the color ladder that has no precondition left to fail.

Devices 1 and 2 each need something — a loadable face, a kept coverage mask. This one needs only
the hit box, so the tests here are mostly about *reach*: that a token the two rungs above refuse
still carries its reading state, and that the mark is drawn without a font.
"""

from __future__ import annotations

import dataclasses

import pytest

from saitenka.subtitles import decoration

RULE = decoration.TokenRule(100, 200, 60, 48, 0x00FF80)


def test_the_mark_names_no_font() -> None:
    r"""The whole reason this device exists. An `\fn` here would reintroduce exactly the failure it
    is the fallback for — mpv's OSD renderer substituting a face it can load."""
    line = decoration.event_line(RULE)

    assert r"\fn" not in line
    assert r"\fs" not in line
    assert r"\p1}" in line


def test_the_rule_sits_under_the_box_not_over_the_glyphs() -> None:
    """mpv's typesetting is what this mode exists to preserve, so the mark goes beside the word."""
    line = decoration.event_line(RULE)

    assert r"\pos(100,249)" in line


def test_the_color_is_written_as_ass_bgr() -> None:
    assert r"\1c&H80FF00&" in decoration.event_line(RULE)


@pytest.mark.parametrize(
    ("height", "expected"),
    [(12, 1), (24, 2), (36, 3), (240, 3), (4, 1)],
)
def test_the_rule_scales_with_the_token_but_is_bounded(height: int, expected: int) -> None:
    """A fixed thickness is invisible on a 4K cue and a highlight on a small one; an unbounded
    proportion turns a large sign's underline into a bar."""
    rule = decoration.TokenRule(0, 0, 60, height, 0xFFFFFF)

    assert rule.thickness == expected
    assert f"l 60 {expected}" in decoration.event_line(rule)


@pytest.mark.parametrize(
    "rule",
    [
        decoration.TokenRule(0, 0, 0, 48, 0xFFFFFF),
        decoration.TokenRule(0, 0, 60, 0, 0xFFFFFF),
        decoration.TokenRule(0, 0, 60, 48, -1),
        decoration.TokenRule(0, 0, 60, 48, 0x1000000),
    ],
)
def test_a_box_that_is_not_a_box_draws_nothing(rule: decoration.TokenRule) -> None:
    """The numbers arrive from a measurement, and a degenerate one must not become a drawing
    command mpv's parser then has to survive."""
    assert rule.drawable is False
    assert decoration.payload([rule]) == ""


def test_a_cue_with_nothing_to_mark_clears_the_slot() -> None:
    assert decoration.payload([]) == ""


def test_a_whitespace_token_is_not_underlined() -> None:
    """Device 3 draws from the box, not the text, so nothing in this module can tell a space from a
    word — the ladder has to drop the token before it reaches this rung."""
    from test_overprint import Style, draw_request

    from saitenka.app.subtitle_render import color_ladder
    from saitenka.app.subtitles import WordBox

    request = draw_request(
        styles=[Style((1, 2, 3, 255))] * 3,
        boxes=[WordBox(1, 60, 0, 50, 40, "", 0.0)],
    )
    request.lines[0][1] = dataclasses.replace(request.lines[0][1], surface=" ")

    assert color_ladder(request).rules == ()


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_libass_draws_the_rule_where_the_box_is() -> None:
    """The oracle: the mark is only useful if it lands under the word it belongs to. Rendered
    through a real libass whose style names a font that does not exist, which is the situation the
    device is for — a drawing does not consult one, so it must still paint."""
    libasslite = pytest.importorskip("libasslite")
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: P,saitenka-no-such-family,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,"
        "100,100,0,0,1,0,0,7,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginV, Effect, Text\n"
    )
    rule = decoration.TokenRule(120, 300, 96, 48, 0xFF0000)
    events = "\n".join(
        f"Dialogue: 0,0:00:00.00,0:00:20.00,P,,0,0,0,,{line}"
        for line in decoration.payload([rule]).splitlines()
    )
    renderer = libasslite.AssRenderer((header + events + "\n").encode())
    try:
        result = renderer.render(1_000, (1280, 720), (1280, 720), pixel_aspect=1.0)
    finally:
        renderer.close()

    painted = [layer for layer in result.layers if layer.image_type == 0 and layer.width > 0]
    assert painted, "libass drew nothing — a font-independent device that needs a font is not one"
    # The origin, not the extent: libass pads a bitmap by a pixel and aligns its stride, so a 96×3
    # rule arrives as a 112×16 image. Where it starts is the claim; how it is packed is libass's.
    assert all(abs(layer.dst_x - 120) <= 1 for layer in painted)
    assert all(300 + 48 <= layer.dst_y + 1 <= 300 + 48 + 8 for layer in painted)
