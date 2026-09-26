from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from saitenka_subtitles import (
    SubtitleTrackId,
    TokenAnnotation,
    UnsupportedAssEvent,
    parse_cues,
    prepare_ass_hit_map_frame,
)
from saitenka_subtitles.colored_track import (
    INJECTED,
    Verbatim,
    color_document,
    colored_origin,
    strip_colors,
)

HEAD = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1280\n"
    "PlayResY: 720\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
    "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,"
    "1,2,10,10,30,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _per_character(text: str):
    """Every visible character is a token, colored by its position."""
    tokens = tuple(
        TokenAnnotation(index, index, index + 1)
        for index, char in enumerate(text)
        if not char.isspace()
    )
    return tokens, {token.token_index: 0x102030 + token.token_index for token in tokens}


def _document(*rows: str, ending: str = "\n") -> str:
    return (HEAD + "".join(row + "\n" for row in rows)).replace("\n", ending)


def test_colors_each_token_and_restores_the_authored_color() -> None:
    source = _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を")

    copy = color_document(source, "external:/x.ass", _per_character)

    row = copy.text.splitlines()[-1]
    assert row == (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
        r"{\1c&H302010&\2c&H302010&}猫{\1c&H00FFFFFF&\2c&H00FFFFFF&}"
        r"{\1c&H312010&\2c&H312010&}を{\1c&H00FFFFFF&\2c&H00FFFFFF&}"
    )
    assert copy.colored == 1


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_the_copy_strips_back_to_its_source_byte_for_byte(ending: str) -> None:
    # Padded margins, a one-digit hour next to a two-digit one, spaces in Name and Effect: all of
    # these are rewritten by the event serializer, which is why the copy splices the Text field.
    source = "﻿" + _document(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default, Speaker ,0010,0000,0000,,猫を見る",
        "Dialogue: 0,00:00:04.00,00:00:06.00,Default,,0,0,0,,犬\\Nも",
        "Comment: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,メモ",
        ending=ending,
    )

    copy = color_document(source, "external:/x.ass", _per_character)

    assert copy.colored == 2
    assert strip_colors(copy.text) == source
    assert parse_cues(copy.text, "x.ass") == parse_cues(source, "x.ass")


def test_the_origin_rides_in_the_script_info_header() -> None:
    source = _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫")

    copy = color_document(source, "embedded:3:/v.mkv", _per_character)

    assert colored_origin(copy.text) == "embedded:3:/v.mkv"
    assert colored_origin(source) is None


@pytest.mark.parametrize(
    "text",
    [
        r"{\fad(100,100)}猫",
        r"{\alpha&H80&}猫",
        r"{\clip(0,0,10,10)}猫",
        r"{\frz10}猫",
        r"{\k20}猫",
    ],
)
def test_an_event_outside_the_static_paint_gate_is_copied_verbatim(text: str) -> None:
    row = f"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{text}"

    copy = color_document(_document(row), "external:/x.ass", _per_character)

    assert copy.text.splitlines()[-1] == row
    assert copy.verbatim == {Verbatim.PAINT_GATE: 1}


def test_an_event_without_tokens_is_copied_verbatim() -> None:
    row = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,♪"

    copy = color_document(_document(row), "external:/x.ass", lambda _text: None)

    assert copy.text.splitlines()[-1] == row
    assert copy.verbatim == {Verbatim.NO_TOKENS: 1}


def test_black_is_painted_one_step_off_the_rewriters_reserved_zero() -> None:
    source = _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫")

    copy = color_document(
        source, "external:/x.ass", lambda _text: ((TokenAnnotation(0, 0, 1),), {0: 0})
    )

    assert r"{\1c&H010101&\2c&H010101&}猫" in copy.text


def test_a_document_already_holding_the_injected_form_is_refused() -> None:
    source = _document(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\1c&H0000FF&\2c&H0000FF&}猫"
    )

    with pytest.raises(UnsupportedAssEvent, match="already carries"):
        color_document(source, "external:/x.ass", _per_character)


def test_a_header_placed_beyond_where_readers_look_is_refused() -> None:
    source = (
        "; " + "x" * 9000 + "\n" + _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫")
    )

    with pytest.raises(UnsupportedAssEvent, match="too far"):
        color_document(source, "external:/x.ass", _per_character)


@pytest.mark.parametrize("separator", ["\x0c", "\u2028", "\x85"])
def test_a_row_is_one_row_to_libass_whatever_unicode_calls_a_line_break(separator: str) -> None:
    source = _document(f"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫{separator}犬")

    copy = color_document(source, "external:/x.ass", _per_character)

    assert r"{\1c&H322010&\2c&H322010&}犬" in copy.text
    assert strip_colors(copy.text) == source


def test_a_document_without_script_info_has_nowhere_to_record_its_origin() -> None:
    source = _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫").replace(
        "[Script Info]", "[Info]"
    )

    with pytest.raises(UnsupportedAssEvent, match="Script Info"):
        color_document(source, "external:/x.ass", _per_character)


def _hit_map(document: str, row_index: int, text: str, tokens):
    rows = [line for line in document.splitlines() if line.startswith("Dialogue:")]
    return prepare_ass_hit_map_frame(
        document.encode(),
        SubtitleTrackId("track"),
        active_rows=rows[row_index],
        text=text,
        tokens=tokens,
    )


def test_the_hit_map_over_the_copy_finds_the_same_tokens() -> None:
    source = _document(r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\b1}猫を 見る")
    tokens, _colors = _per_character("猫を 見る")

    copy = color_document(source, "external:/x.ass", _per_character)
    original = _hit_map(source, 0, "猫を 見る", tokens)
    colored = _hit_map(copy.text, 0, "猫を 見る", tokens)

    assert [entry.token_index for entry in colored.palette] == [
        entry.token_index for entry in original.palette
    ]
    assert colored.paint_qualification is original.paint_qualification


def test_a_live_token_coarser_than_the_copy_refuses_the_hit_map() -> None:
    # The copy colored 見 and る apart; live now reads 見る as one token. The restore block between
    # them lands inside it, so the frame loses its boxes instead of measuring the wrong span.
    source = _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,見る")
    copy = color_document(source, "external:/x.ass", _per_character)

    with pytest.raises(UnsupportedAssEvent, match="crosses a token"):
        _hit_map(copy.text, 0, "見る", (TokenAnnotation(0, 0, 2),))


def test_a_live_token_finer_than_the_copy_keeps_the_hit_map() -> None:
    source = _document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,見る")
    whole = (TokenAnnotation(0, 0, 2),)
    copy = color_document(source, "external:/x.ass", lambda _text: (whole, {0: 0x123456}))

    frame = _hit_map(copy.text, 0, "見る", (TokenAnnotation(0, 0, 1), TokenAnnotation(1, 1, 2)))

    assert [entry.token_index for entry in frame.palette] == [0, 1]


_TEXT = st.text(alphabet="猫犬見る を、。 AB\\N{}", min_size=1, max_size=12)


@given(texts=st.lists(_TEXT, min_size=1, max_size=4), crlf=st.booleans())
@example(texts=["{\\b1}猫", "{"], crlf=False)
def test_every_copy_strips_back_to_its_source(texts: list[str], *, crlf: bool) -> None:
    rows = [
        f"Dialogue: 0,0:00:0{i}.00,0:00:0{i + 1}.00,Default,,0,0,0,,{t}"
        for i, t in enumerate(texts)
    ]
    source = _document(*rows, ending="\r\n" if crlf else "\n")
    if INJECTED.search(source):
        return

    copy = color_document(source, "external:/x.ass", _per_character)

    assert strip_colors(copy.text) == source
    assert parse_cues(copy.text, "x.ass") == parse_cues(source, "x.ass")


def test_an_event_its_scorer_cannot_place_is_copied_verbatim() -> None:
    source = _document(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,犬",
    )

    def colors(text: str):
        if text == "犬":
            raise ValueError("token lines exceed subtitle semantic text")
        return _per_character(text)

    copy = color_document(source, "external:/x.ass", colors)

    assert (copy.colored, copy.verbatim) == (1, {Verbatim.REFUSED: 1})
