"""The per-token color drawn over mpv's own subtitle pixels.

The payload's job is to put the same glyph, in the same face, at the same place — only a different
color. So the oracle is not "does the string look right" but "does drawing it land where the
measurement said the token was", which the last test asks of a real libass.
"""

from __future__ import annotations

import dataclasses

import pytest

from saitenka.subtitles import overprint

PAINT = overprint.TokenPaint("猫", 100, 200, "Noto Sans JP", 48.0, 0x00FF80)


def test_a_token_is_placed_top_left_at_its_measured_origin() -> None:
    """`\\an7` and `\\pos` together, so the payload never inherits the OSD track's own alignment or
    margins — the measurement is in frame coordinates and this puts it there unchanged."""
    line = overprint.event_line(PAINT)

    assert line.startswith(r"{\an7\pos(100,200)")
    assert r"\fnNoto Sans JP" in line
    assert r"\fs48" in line
    assert line.endswith("}猫")


def test_the_color_is_written_as_ass_bgr() -> None:
    """ASS stores colors byte-reversed. Getting this wrong silently swaps red and blue, which reads
    as a palette choice rather than a bug."""
    assert r"\1c&H80FF00&" in overprint.event_line(PAINT)


def test_the_authored_border_is_not_reproduced_and_ours_is_explicit() -> None:
    """mpv keeps drawing the authored outline and shadow; reproducing them slightly wrong is worse
    than leaving them. Our own border is a separate, deliberate hairline."""
    line = overprint.event_line(overprint.TokenPaint("猫", 0, 0, "Arial", 20.0, 0xFFFFFF, 1.5))

    assert r"\bord1.5" in line
    assert r"\shad0" in line


@pytest.mark.parametrize(
    "paint",
    [
        overprint.TokenPaint("猫", 0, 0, "", 48.0, 0xFFFFFF),  # no measured face
        overprint.TokenPaint("猫", 0, 0, "Arial", 0.0, 0xFFFFFF),  # no measured size
        overprint.TokenPaint("  ", 0, 0, "Arial", 48.0, 0xFFFFFF),  # nothing to paint
        overprint.TokenPaint("{\\b1}", 0, 0, "Arial", 48.0, 0xFFFFFF),  # ASS syntax
        overprint.TokenPaint("a\\Nb", 0, 0, "Arial", 48.0, 0xFFFFFF),
    ],
)
def test_a_token_that_cannot_be_drawn_faithfully_is_not_drawn(paint: overprint.TokenPaint) -> None:
    """Drawing at a guess puts the wrong glyph shape over the right word, and the user cannot tell
    it is wrong. Uncolored is the honest outcome; escaping the syntax is not, because it changes
    what libass lays out and the advances stop matching mpv's."""
    assert paint.drawable is False
    assert overprint.payload([paint]) == ""


def test_a_cue_with_nothing_drawable_clears_the_slot() -> None:
    """Empty is a real answer: the caller sends it, and an empty payload removes the previous cue's
    colors instead of leaving them over this one's words."""
    assert overprint.payload([]) == ""


def test_every_drawable_token_gets_its_own_event() -> None:
    """Per token, not per line. One run would accumulate advances and drift from mpv's layout; a
    `\\pos`-ed glyph cannot."""
    paints = [
        overprint.TokenPaint("猫", 0, 0, "Arial", 48.0, 0x111111),
        overprint.TokenPaint("を", 50, 0, "Arial", 48.0, 0x222222),
        overprint.TokenPaint("", 90, 0, "Arial", 48.0, 0x333333),
    ]

    lines = overprint.payload(paints).splitlines()

    assert len(lines) == 2
    assert all(line.startswith("{") for line in lines)


def draw_request(*, styles, boxes):
    from saitenka.app.subtitle_render import DrawRequest
    from saitenka.app.tokenize import Token

    surfaces = ["猫", "を", "見る"]
    tokens = [Token(s, s, s, "名詞", i, i + 1) for i, s in enumerate(surfaces)]
    return DrawRequest(
        text="".join(surfaces),
        lines=[tokens],
        osd=(1280, 720),
        sub_size=44,
        bg_opacity=150,
        bottom_margin=40,
        secondary_role=False,
        upgrade_pending=False,
        annotation_degraded=False,
        annotation_visible=True,
        hover=-1,
        hover_span=None,
        styles=styles,
        boxes=boxes,
    )


class Style:
    """Stands in for `scoring.TokenStyle`, which carries a reading-state color AND a level
    underline. Both, because the two are additive and the native path has to draw both."""

    def __init__(self, color, underline=None) -> None:
        self.color = color
        self.underline = underline


def measured_boxes(*, font: str = "Arial", size: float = 48.0):
    from saitenka.app.subtitles import WordBox

    return [WordBox(index, 100 + index * 60, 600, 50, 40, font, size) for index in range(3)]


def test_the_cue_is_drawn_once_per_token_in_its_own_color() -> None:
    """The feature: mpv keeps drawing the cue, and each token is drawn again over it in the color
    its reading state calls for — one `\\pos`-ed event per token, at the measured origin."""
    from saitenka.app.subtitle_render import overprint_payload

    styles = [Style((255, 0, 0, 255)), Style((0, 255, 0, 255)), Style((0, 0, 255, 255))]

    payload = overprint_payload(draw_request(styles=styles, boxes=measured_boxes()))

    lines = payload.splitlines()
    assert len(lines) == 3
    assert r"\pos(100,600)" in lines[0] and r"\1c&H0000FF&" in lines[0]
    assert r"\pos(220,600)" in lines[2] and r"\1c&HFF0000&" in lines[2]
    assert lines[0].endswith("}猫")


def test_a_cue_the_measurement_gave_no_face_for_is_left_uncolored() -> None:
    """No face and no mask means the color has nowhere truthful to go, so it goes nowhere.

    Marking the box instead was the earlier answer, and it drew the same rule the JLPT level draws —
    one underline meaning two things, in the mode where the level's own underline was missing. The
    token keeps its box, its tooltip and its mining; only the color is absent."""
    from saitenka.app.subtitle_render import overprint_payload

    styles = [Style((255, 0, 0, 255))] * 3

    payload = overprint_payload(draw_request(styles=styles, boxes=measured_boxes(font="")))

    assert payload == ""


def test_a_cue_with_no_reading_state_is_left_uncolored() -> None:
    """The legacy renderer paints the color itself, so it produces no faces and no overprint; and a
    cue whose annotation has not landed has no color to paint yet."""
    from saitenka.app.subtitle_render import overprint_payload

    assert overprint_payload(draw_request(styles=None, boxes=measured_boxes())) == ""


def test_a_level_underline_is_drawn_beside_the_color_not_instead_of_it() -> None:
    """JLPT level and reading state are additive in `scoring.py`, and the standard renderer has
    always drawn both. Reading only `style.color` is what made the level invisible whenever this
    mode was on — a whole feature silently absent, with nothing reporting it."""
    from saitenka.app.subtitle_render import color_ladder, overprint_payload

    styles = [Style((255, 0, 0, 255), underline=(0, 128, 255, 255))] * 3
    request = draw_request(styles=styles, boxes=measured_boxes())

    ladder = color_ladder(request)
    payload = overprint_payload(request)

    assert len(ladder.paints) == 3, "the color device stood down"
    assert len(ladder.rules) == 3, "the level underline never reached the payload"
    # Both marks, in their own colors: the word redrawn in red, the rule under it in blue.
    assert payload.count(r"\p1}") == 3
    assert r"\1c&HFF8000&" in payload, "the rule did not carry the level's own color"


def test_a_token_with_no_level_gets_no_rule() -> None:
    """The negative control for the one above: a rule that appeared for every token would look
    like a level mark on words that have none, which is the same lie in the other direction."""
    from saitenka.app.subtitle_render import color_ladder

    ladder = color_ladder(
        draw_request(styles=[Style((255, 0, 0, 255))] * 3, boxes=measured_boxes())
    )

    assert (len(ladder.paints), len(ladder.rules)) == (3, 0)


def test_each_token_lands_on_at_most_one_color_device() -> None:
    """The devices are one decision, not independent filters over the same boxes — which is how a
    resolved face plus ASS syntax in the text was refused by device 1 for its text and by device 2
    for its face, and lost its color silently. A token neither can serve gets nothing, and `rules`
    stays empty because no token here carries a level."""
    from saitenka.app.subtitle_render import color_ladder
    from saitenka.app.subtitles import WordBox

    boxes = [
        WordBox(0, 0, 0, 50, 40, "Arial", 48.0),  # a face: device 1
        WordBox(1, 60, 0, 50, 40, "", 48.0, coverage=bytes(50 * 40)),  # a mask: device 2
        WordBox(2, 120, 0, 50, 40, "", 0.0),  # neither: uncolored
    ]

    ladder = color_ladder(draw_request(styles=[Style((1, 2, 3, 255))] * 3, boxes=boxes))

    assert (len(ladder.paints), len(ladder.masks), len(ladder.rules)) == (1, 1, 0)


def test_a_face_the_overprint_cannot_use_still_reaches_the_raster() -> None:
    """The token that used to fall out: device 1 refuses the *text*, so having a face must not keep
    it off device 2, which does not care what the text says."""
    from saitenka.app.subtitle_render import color_ladder
    from saitenka.app.subtitles import WordBox

    boxes = [WordBox(0, 0, 0, 4, 4, "Arial", 48.0, coverage=bytes(16))]
    request = draw_request(styles=[Style((1, 2, 3, 255))], boxes=boxes)
    request.lines[0][0] = dataclasses.replace(request.lines[0][0], surface=r"{\b1}")

    ladder = color_ladder(request)

    assert (len(ladder.paints), len(ladder.masks), len(ladder.rules)) == (0, 1, 0)


def test_a_box_without_a_token_behind_it_is_skipped_not_mispainted() -> None:
    """Indices come from two sides — the measurement and the tokenizer — and a cue re-tokenized
    under a stale snapshot would otherwise paint one token's color onto another's text."""
    from saitenka.app.subtitle_render import overprint_payload

    boxes = [*measured_boxes(), type(measured_boxes()[0])(9, 0, 0, 10, 10, "Arial", 48.0)]

    payload = overprint_payload(draw_request(styles=[Style((1, 2, 3, 255))] * 3, boxes=boxes))

    assert len(payload.splitlines()) == 3


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_the_payload_lands_where_the_measurement_said_the_token_was() -> None:
    """The oracle that matters: render the overprint through libass and check each token's ink sits
    at the origin the payload asked for. A payload that placed, sized or escaped anything wrongly
    puts color beside the word rather than on it."""
    libasslite = pytest.importorskip("libasslite")
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: P,sans-serif,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
        "1,0,0,7,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    origins = [(120, 300), (200, 300), (280, 300)]
    paints = [
        overprint.TokenPaint(text, x, y, "sans-serif", 48.0, 0xFF0000)
        for text, (x, y) in zip("猫を犬", origins, strict=True)
    ]
    events = "\n".join(
        f"Dialogue: 0,0:00:00.00,0:00:20.00,P,,0,0,0,,{line}"
        for line in overprint.payload(paints).splitlines()
    )
    renderer = libasslite.AssRenderer((header + events + "\n").encode())
    try:
        result = renderer.render(1_000, (1280, 720), (1280, 720), pixel_aspect=1.0)
    finally:
        renderer.close()

    painted = [layer for layer in result.layers if layer.image_type == 0 and layer.width > 0]
    assert painted, "libass drew nothing — the payload is not a renderable document"
    for x, y in origins:
        # Ink starts a little inside its origin box (bearings), so the assertion is that some glyph
        # begins near each requested origin — not that it begins exactly on it.
        assert any(x <= layer.dst_x < x + 48 and y <= layer.dst_y < y + 48 for layer in painted), (
            f"no glyph near the requested origin {(x, y)}"
        )
