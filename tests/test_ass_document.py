from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from saitenka.subtitles import (
    AnnotatedSubtitleEvent,
    AssStyle,
    AssStyleCatalog,
    DecodedSubtitleEvent,
    RawSubtitleEvent,
    SubtitleEventId,
    SubtitleTrackId,
    TokenAnnotation,
    UnsupportedAssEvent,
    allocate_token_colors,
    decode_ass_event,
    parse_ass_event_line,
    parse_ass_styles,
    rewrite_ass_event,
    serialize_ass_event_line,
)

TRACK = SubtitleTrackId("external:/tmp/example.ass:1")
CATALOG = AssStyleCatalog((AssStyle("Default", "00FFFFFF"), AssStyle("Alt", "00112233")))
_PRIMARY = re.compile(r"\\1?c&H([0-9A-Fa-f]{1,8})&?(?![0-9A-Fa-f])")
_MATRIX_CATALOG = AssStyleCatalog(
    tuple(AssStyle(name, "00FFFFFF") for name in ("Default", "Sign", "Box"))
)


def event(
    raw: str, *, source_order: int = 0, style: str = "Default", effect: str = ""
) -> RawSubtitleEvent:
    identity = SubtitleEventId(TRACK, 1000, 2500, 2, source_order)
    return RawSubtitleEvent(identity, raw, style, "Narrator", effect, (12, 34, 56))


def annotated(
    raw: str,
    *spans: tuple[int, int],
    source_order: int = 0,
    style: str = "Default",
    effect: str = "",
) -> AnnotatedSubtitleEvent:
    decoded = decode_ass_event(event(raw, source_order=source_order, style=style, effect=effect))
    return AnnotatedSubtitleEvent(
        decoded,
        tuple((TokenAnnotation(index, start, end) for index, (start, end) in enumerate(spans))),
    )


def effective_character_colors(raw: str, initial: str = "00FFFFFF") -> tuple[str, ...]:
    """Independent oracle over the emitted primary-color state."""
    colors: list[str] = []
    color = initial
    index = 0
    while index < len(raw):
        if raw[index] == "{":
            end = raw.find("}", index + 1)
            if end >= 0:
                for match in _PRIMARY.finditer(raw[index + 1 : end]):
                    color = match.group(1).upper()
                index = end + 1
                continue
        if raw[index] == "\\" and index + 1 < len(raw) and (raw[index + 1] in "Nnh"):
            colors.append(color)
            index += 2
        else:
            colors.append(color)
            index += 1
    return tuple(colors)


def annotations_from_authored_color_boundaries(
    raw_event: RawSubtitleEvent,
) -> AnnotatedSubtitleEvent:
    decoded = decode_ass_event(raw_event)
    boundaries: list[int] = []
    index = 0
    while index < len(raw_event.raw_text):
        if raw_event.raw_text[index] == "{":
            end = raw_event.raw_text.find("}", index + 1)
            if end >= 0:
                if _PRIMARY.search(raw_event.raw_text[index + 1 : end]):
                    boundaries.append(end + 1)
                index = end + 1
                continue
        index += 1
    starts = [
        next(
            (offset for offset, span in enumerate(decoded.raw_spans) if span.start >= boundary),
            len(decoded.text),
        )
        for boundary in boundaries
    ]
    if not starts or starts[0] != 0 or len(starts) != len(set(starts)):
        raise AssertionError("matrix event lacks one authored color boundary per token")
    ends = (*starts[1:], len(decoded.text))
    return AnnotatedSubtitleEvent(
        decoded,
        tuple(
            TokenAnnotation(token, start, end)
            for token, (start, end) in enumerate(zip(starts, ends, strict=True))
        ),
    )


@pytest.mark.parametrize(
    ("raw", "text", "source_fragments", "drawings"),
    [
        ("猫\\N犬\\h鳥", "猫\n犬 鳥", ("猫", "\\N", "犬", "\\h", "鳥"), ()),
        ("{\\i1}猫{\\b1}犬{\\i0}", "猫犬", ("猫", "犬"), ()),
        ("{\\p1}m 0 0 l 10 10{\\p0}字", "字", ("字",), ((5, 18, 1),)),
        ("猫{broken", "猫{broken", tuple("猫{broken"), ()),
    ],
)
def test_decode_ass_text_keeps_exact_raw_spans(
    raw: str,
    text: str,
    source_fragments: tuple[str, ...],
    drawings: tuple[tuple[int, int, int], ...],
) -> None:
    decoded = decode_ass_event(event(raw))
    assert decoded.text == text
    assert tuple(raw[span.start : span.end] for span in decoded.raw_spans) == source_fragments
    assert tuple((span.start, span.end, span.scale) for span in decoded.drawings) == drawings


def test_raw_spans_do_not_guess_one_boundary_across_an_override() -> None:
    decoded = decode_ass_event(event("猫{\\b1}犬"))
    assert tuple((span.start, span.end) for span in decoded.raw_spans) == ((0, 1), (6, 7))


def test_legacy_offsets_do_not_masquerade_as_exact_spans() -> None:
    source = event(r"猫{\c&HABCDEF&}犬")
    decoded = DecodedSubtitleEvent(source, "猫犬", (0, 1, len(source.raw_text)))
    annotated_event = AnnotatedSubtitleEvent(decoded, (TokenAnnotation(0, 1, 2),))
    with pytest.raises(UnsupportedAssEvent, match="exact raw text spans"):
        rewrite_ass_event(annotated_event, {0: 0x010203}, CATALOG)


def test_uppercase_drawing_command_remains_semantic_text() -> None:
    decoded = decode_ass_event(event(r"{\P1}m 0 0 l 20 20{\P0}猫"))
    assert decoded.text == "m 0 0 l 20 20猫"


def test_parse_ass_styles_uses_authored_format_order() -> None:
    extradata = (
        b"[V4+ Styles]\nFormat: Fontname, PrimaryColour, Name\nStyle: Arial,&H00ABCDEF,Default\n"
    )
    assert parse_ass_styles(extradata) == AssStyleCatalog(
        (AssStyle("Default", "00ABCDEF", "Arial"),)
    )


def test_a_style_without_a_usable_size_still_parses() -> None:
    """The size only matters to the overprint. A style whose color parses is still one the hit map
    can use, so an unparseable `Fontsize` costs the overprint, not the interaction."""
    extradata = (
        b"[V4+ Styles]\nFormat: Name, PrimaryColour, Fontname, Fontsize\n"
        b"Style: Default,&H00ABCDEF,Arial,not-a-number\n"
    )

    catalog = parse_ass_styles(extradata)

    assert catalog.styles[0].font_size == 0.0
    assert catalog.styles[0].font_name == "Arial"


def test_a_styles_face_and_size_are_read_for_the_overprint() -> None:
    extradata = (
        b"[V4+ Styles]\nFormat: Name, PrimaryColour, Fontname, Fontsize\n"
        b"Style: Default,&H00ABCDEF,Noto Sans JP,48.5\n"
    )

    style = parse_ass_styles(extradata).styles[0]

    assert (style.font_name, style.font_size) == ("Noto Sans JP", 48.5)


def test_style_rows_and_identities_follow_libass_case_sensitivity() -> None:
    extradata = b"[V4+ Styles]\nFormat: Name, PrimaryColour\nstyle: Alt,&H00112233\n"
    with pytest.raises(UnsupportedAssEvent, match="no styles"):
        parse_ass_styles(extradata)
    catalog = AssStyleCatalog((AssStyle("Alt", "00112233"), AssStyle("alt", "00445566")))
    assert catalog.primary_color("Alt") == "00112233"
    assert catalog.primary_color("alt") == "00445566"


def test_event_line_round_trip_preserves_identity_metadata_and_raw_tags() -> None:
    line = "Dialogue: 2,0:00:01.00,0:00:02.50,Default,Narrator,12,34,56,banner,{\\an7\\alpha&H40&}猫,犬"
    parsed = parse_ass_event_line(line, TRACK, source_order=9)
    assert serialize_ass_event_line(parsed) == line
    assert parsed.identity == SubtitleEventId(TRACK, 1000, 2500, 2, 9)
    assert (parsed.style, parsed.actor, parsed.effect, parsed.margins) == (
        "Default",
        "Narrator",
        "banner",
        (12, 34, 56),
    )


@pytest.mark.parametrize(
    "fields",
    [
        (
            *("layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect"),
            "vendor",
            "text",
        ),
        (
            "layer",
            "start",
            "end",
            "style",
            "actor",
            "marginl",
            "marginr",
            "marginv",
            "effect",
            "text",
        ),
    ],
)
def test_event_line_rejects_formats_it_cannot_round_trip(fields: tuple[str, ...]) -> None:
    values = ["0", "0:00:01.00", "0:00:02.00", "Default", "Narrator", "0", "0", "0", ""]
    if "vendor" in fields:
        values.append("keep-me")
    values.append("猫")
    with pytest.raises(UnsupportedAssEvent, match="canonical V4\\+"):
        parse_ass_event_line("Dialogue: " + ",".join(values), TRACK, 0, fields=fields)


@pytest.mark.parametrize("kind", ["dialogue", "DIALOGUE"])
def test_event_line_requires_libass_dialogue_spelling(kind: str) -> None:
    line = f"{kind}: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,猫"
    with pytest.raises(UnsupportedAssEvent, match="Dialogue"):
        parse_ass_event_line(line, TRACK, 0)


@pytest.mark.parametrize(
    "authored",
    [
        "Dialogue: 0,0:00:00.50,0:00:09.00,Default,,0,0,0,,{\\i1}こんにちは{\\i0}",
        "Dialogue: 0,0:00:00.50,0:00:09.00,Default,,0000,0000,0000,,{\\i1}こんにちは{\\i0}",
    ],
)
def test_margin_zero_padding_does_not_change_an_event_signature(authored: str) -> None:
    """mpv reports margins zero-padded (`0000`) where a file on disk writes `0`, so this
    normalization is the only reason an authored source ever matches what mpv says is on screen.
    Verified against a real mpv 0.40 on an embedded ASS track extracted with ``-c:s copy``.
    """
    from saitenka.subtitles.ass_geometry import _event_signature

    reported = "Dialogue: 0,0:00:00.50,0:00:09.00,Default,,0000,0000,0000,,{\\i1}こんにちは{\\i0}"

    assert _event_signature(parse_ass_event_line(authored, TRACK, 0)) == _event_signature(
        parse_ass_event_line(reported, TRACK, 0)
    )


def test_a_differing_margin_still_changes_the_signature() -> None:
    """The negative control: normalization must not flatten margins into no signal at all."""
    from saitenka.subtitles.ass_geometry import _event_signature

    base = "Dialogue: 0,0:00:00.50,0:00:09.00,Default,,0,0,0,,猫"
    moved = "Dialogue: 0,0:00:00.50,0:00:09.00,Default,,0,0,90,,猫"

    assert _event_signature(parse_ass_event_line(base, TRACK, 0)) != _event_signature(
        parse_ass_event_line(moved, TRACK, 0)
    )


_SHIFT_DOC = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold,\
 Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,\
 Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sign,Yasashisa,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,\
10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Sign,,0,0,0,,{\\an8\\fad(200,200)}早い
Dialogue: 0,0:00:30.00,0:00:32.50,Sign,,0,0,0,,遅い
"""


def test_shifting_dialogue_moves_only_events_from_the_boundary_on() -> None:
    from saitenka.subtitles.ass import shift_ass_dialogue

    out = shift_ass_dialogue(_SHIFT_DOC, delta_ms=4732, from_ms=20_000)

    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Sign,,0,0,0,,{\\an8\\fad(200,200)}早い" in out
    assert "Dialogue: 0,0:00:34.73,0:00:37.23,Sign,,0,0,0,,遅い" in out


def test_shifting_dialogue_leaves_the_typesetting_byte_identical() -> None:
    """The whole reason a re-time stopped serializing cues to SRT: an ASS is chosen FOR its styles,
    and the old path returned a SubRip body under a name that still said `.ass`."""
    from saitenka.subtitles.ass import shift_ass_dialogue

    out = shift_ass_dialogue(_SHIFT_DOC, delta_ms=1_000, from_ms=0)

    for line in _SHIFT_DOC.splitlines():
        if not line.startswith("Dialogue:"):
            assert line in out


def test_shifting_dialogue_clamps_the_pair_and_keeps_the_duration() -> None:
    """A shift past zero clamps the event as a PAIR: clamping each end independently would collapse
    it to an empty range, which `SubtitleEventId` rejects outright — a re-time must not be able to
    produce a document that is not a legal one."""
    from saitenka.subtitles.ass import shift_ass_dialogue

    out = shift_ass_dialogue(_SHIFT_DOC, delta_ms=-9_999_999, from_ms=0)

    assert "Dialogue: 0,0:00:00.00,0:00:01.00,Sign,,0,0,0,," in out  # 1s cue keeps its 1s
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,Sign,,0,0,0,," in out  # 2.5s cue keeps its 2.5s


def test_shifting_dialogue_refuses_a_document_it_cannot_round_trip() -> None:
    """A non-canonical event Format would be silently REORDERED by serialization, so the caller has
    to keep the original instead — raising is what lets it."""
    from saitenka.subtitles.ass import shift_ass_dialogue

    doc = _SHIFT_DOC.replace(
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Vendor, Text",
    )
    with pytest.raises(UnsupportedAssEvent):
        shift_ass_dialogue(doc, delta_ms=1_000, from_ms=0)


def test_event_line_preserves_authored_leading_and_trailing_text_spaces() -> None:
    line = "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,  猫  "
    parsed = parse_ass_event_line(line, TRACK, source_order=0)
    assert parsed.raw_text == "  猫  "
    assert serialize_ass_event_line(parsed) == line


@pytest.mark.parametrize(
    "line",
    [
        "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,text",
        "Dialogue: 0,not-time,0:00:02.00,Default,,0,0,0,,text",
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,missing",
    ],
)
def test_event_line_parser_rejects_rows_it_cannot_preserve(line: str) -> None:
    with pytest.raises(UnsupportedAssEvent):
        parse_ass_event_line(line, TRACK, source_order=0)


@pytest.mark.parametrize(
    "extradata",
    [
        b"[Script Info]\nTitle: no styles\n",
        b"[V4+ Styles]\nFormat: Name, PrimaryColour\nStyle: Default,nope\n",
    ],
)
def test_parse_ass_styles_rejects_missing_or_malformed_primary_colors(extradata: bytes) -> None:
    with pytest.raises(UnsupportedAssEvent):
        parse_ass_styles(extradata)


def test_rewrite_preserves_metadata_raw_tags_and_semantic_text() -> None:
    source = annotated("{\\an7\\alpha&H40&}猫{\\b1}を見る{\\b0}", (0, 1), (1, 4))
    rewrite = rewrite_ass_event(source, {0: 0x123456, 1: 0x654321}, CATALOG)
    assert decode_ass_event(rewrite.event).text == source.decoded.text
    assert rewrite.restore() == source.decoded.source
    assert rewrite.event.identity == source.decoded.source.identity
    assert (
        rewrite.event.style,
        rewrite.event.actor,
        rewrite.event.effect,
        rewrite.event.margins,
    ) == ("Default", "Narrator", "", (12, 34, 56))
    assert "{\\an7\\alpha&H40&}" in rewrite.event.raw_text
    assert "{\\b1}" in rewrite.event.raw_text
    assert "{\\b0}" in rewrite.event.raw_text


def test_every_token_gets_its_visible_color_and_source_color_is_restored() -> None:
    source = annotated("{\\c&HABCDEF&}猫を見る犬", (0, 1), (1, 4), (4, 5))
    rewrite = rewrite_ass_event(source, {0: 0x010203, 1: 0x102030, 2: 0xA0B0C0}, CATALOG)
    assert effective_character_colors(rewrite.event.raw_text) == (
        "010203",
        "102030",
        "102030",
        "102030",
        "A0B0C0",
    )
    assert rewrite.event.raw_text.endswith("{\\1c&HABCDEF&}")


def test_every_gate_a_required_core_event_rewrites_losslessly() -> None:
    manifest = json.loads(
        Path("tests/fixtures/libass_token_matrix.json").read_text(encoding="utf-8")
    )
    required = [case for case in manifest["cases"] if case["expectation"] == "required-core"]

    assert len(required) == 12
    for case in required:
        palette_by_event: dict[str, dict[int, int]] = {}
        for item in case["palette"]:
            palette_by_event.setdefault(item["event_id"], {})[item["token_index"]] = int(
                item["rgb"], 16
            )
        for source_order, event_row in enumerate(case["visible_events"]):
            source = parse_ass_event_line(f"Dialogue: {event_row}", TRACK, source_order)
            annotated_event = annotations_from_authored_color_boundaries(source)
            colors = palette_by_event[source.actor]

            rewrite = rewrite_ass_event(
                annotated_event,
                colors,
                _MATRIX_CATALOG,
                require_unique=True,
            )

            assert rewrite.restore() == source, case["id"]
            assert decode_ass_event(rewrite.event).text == annotated_event.decoded.text, case["id"]
            observed = effective_character_colors(rewrite.event.raw_text)
            for token in annotated_event.tokens:
                expected = f"{colors[token.token_index]:06X}"
                assert set(observed[token.text_start : token.text_end]) == {expected}, case["id"]


def test_every_gate_a_fallback_candidate_is_rejected() -> None:
    manifest = json.loads(
        Path("tests/fixtures/libass_token_matrix.json").read_text(encoding="utf-8")
    )
    fallback = [case for case in manifest["cases"] if case["expectation"] == "fallback-candidate"]

    assert len(fallback) == 7
    for case in fallback:
        for source_order, event_row in enumerate(case["visible_events"]):
            source = parse_ass_event_line(f"Dialogue: {event_row}", TRACK, source_order)
            annotated_event = annotations_from_authored_color_boundaries(source)
            colors = {
                item["token_index"]: int(item["rgb"], 16)
                for item in case["palette"]
                if item["event_id"] == source.actor
            }

            with pytest.raises(UnsupportedAssEvent):
                rewrite_ass_event(
                    annotated_event,
                    colors,
                    _MATRIX_CATALOG,
                    require_unique=True,
                )


@pytest.mark.parametrize(
    "control",
    ["\N{RIGHT-TO-LEFT OVERRIDE}", "\N{RIGHT-TO-LEFT ISOLATE}", "\N{FIRST STRONG ISOLATE}"],
)
def test_bidi_controls_fail_closed(control: str) -> None:
    source = annotated(f"猫{control}犬", (0, 3))

    with pytest.raises(UnsupportedAssEvent, match="bidirectional text"):
        rewrite_ass_event(source, {0: 0x010203}, CATALOG)


def test_injection_oracle_detects_an_omitted_token_override() -> None:
    source = annotated("猫犬", (0, 1), (1, 2))
    rewrite = rewrite_ass_event(source, {0: 0x010203, 1: 0x102030}, CATALOG)
    broken = rewrite.event.raw_text.replace("{\\1c&H102030&}", "", 1)
    assert effective_character_colors(broken) == ("010203", "00FFFFFF")


def test_injection_oracle_detects_a_shifted_token_boundary() -> None:
    source = annotated("猫犬", (0, 1), (1, 2))
    rewrite = rewrite_ass_event(source, {0: 0x010203, 1: 0x102030}, CATALOG)
    broken = rewrite.event.raw_text.replace("{\\1c&H010203&}猫", "猫{\\1c&H010203&}", 1)
    assert effective_character_colors(broken) == ("00FFFFFF", "102030")


def test_source_reset_between_tokens_uses_the_named_style_color() -> None:
    source = annotated("猫{\\rAlt}犬", (0, 1), (1, 2))
    rewrite = rewrite_ass_event(source, {0: 0x010203, 1: 0x102030}, CATALOG)
    assert rewrite.event.raw_text.endswith("犬{\\1c&H00112233&}")


def test_parameterless_primary_color_reset_restores_the_active_style() -> None:
    source = annotated("{\\rAlt\\c&HABCDEF&}猫{\\c}犬", (0, 1), (1, 2), style="Default")
    rewrite = rewrite_ass_event(source, {0: 0x010203, 1: 0x102030}, CATALOG)
    assert rewrite.event.raw_text.endswith("犬{\\1c&H00112233&}")


def test_unterminated_source_color_is_restored_after_the_token() -> None:
    source = annotated("{\\c&H112233}猫 犬", (0, 1))
    rewrite = rewrite_ass_event(source, {0: 0x010203}, CATALOG)
    assert effective_character_colors(rewrite.event.raw_text) == ("010203", "112233", "112233")


@pytest.mark.parametrize("override", [r"\c112233", r"\c&112233&", r"\c&H112233"])
def test_libass_color_shorthands_are_restored_after_the_token(override: str) -> None:
    source = annotated(f"{{{override}}}猫 犬", (0, 1))
    rewrite = rewrite_ass_event(source, {0: 0x010203}, CATALOG)
    assert effective_character_colors(rewrite.event.raw_text) == ("010203", "112233", "112233")


@pytest.mark.parametrize("override", [r"\c&H", r"\c&H&"])
def test_libass_empty_hex_color_is_restored_as_black(override: str) -> None:
    source = annotated(f"{{{override}}}猫 犬", (0, 1))
    rewrite = rewrite_ass_event(source, {0: 0x010203}, CATALOG)
    assert effective_character_colors(rewrite.event.raw_text) == ("010203", "0", "0")


def test_uppercase_color_command_is_ignored_like_libass() -> None:
    source = annotated(r"{\C&H112233&}猫 犬", (0, 1))
    rewrite = rewrite_ass_event(source, {0: 0x010203}, CATALOG)
    assert effective_character_colors(rewrite.event.raw_text) == ("010203", "00FFFFFF", "00FFFFFF")


def test_animated_effect_field_fails_closed() -> None:
    source = annotated("猫", (0, 1), effect="Banner;10;0;0")
    with pytest.raises(UnsupportedAssEvent, match="effects"):
        rewrite_ass_event(source, {0: 0x010203}, CATALOG)


@pytest.mark.parametrize(
    ("raw", "span", "reason"),
    [
        ("{\\k20}歌う", (0, 2), "animated or karaoke"),
        ("{\\kt20}歌う", (0, 2), "animated or karaoke"),
        ("{\\fad(100,100)}猫", (0, 1), "animated or karaoke"),
        ("{\\fade(0,255,0,0,100,200,300)}猫", (0, 1), "animated or karaoke"),
        ("{\\t(\\fscx120)}動く", (0, 2), "animated or karaoke"),
        ("{\\blur4}猫", (0, 1), "extent is not the word"),
        ("{\\be2}猫", (0, 1), "extent is not the word"),
        ("{\\blur}猫", (0, 1), "extent is not the word"),
        # A sign with no digits: the amount group matches, and reading it as a number does not.
        ("{\\blur-}猫", (0, 1), "extent is not the word"),
        ("{\\be-.}猫", (0, 1), "extent is not the word"),
        ("{\\p1}m 0 0{\\p0}字", (0, 1), "drawing events"),
        ("色{\\r}変更", (0, 3), "crosses a token"),
        ("色{\\c&H112233&}変更", (0, 3), "crosses a token"),
        ("色{\\c&H112233}変更", (0, 3), "crosses a token"),
        ("色{\\cZZ}変更", (0, 3), "unparsed primary-color"),
        ("色{\\c&H123456789}変更", (0, 3), "unparsed primary-color"),
        ("猫{broken", (0, 8), "unclosed"),
    ],
)
def test_rewrite_fails_closed_for_unsupported_ass(
    raw: str, span: tuple[int, int], reason: str
) -> None:
    source = annotated(raw, span)
    with pytest.raises(UnsupportedAssEvent, match=reason):
        rewrite_ass_event(source, {0: 0x010203}, CATALOG)


@pytest.mark.parametrize("raw", ["{\\blur0}猫", "{\\be0}猫", "{\\blur0.0}猫"])
def test_a_blur_that_spreads_nothing_is_not_a_refusal(raw: str) -> None:
    """`\\blur0` and `\\be0` are ordinary in real typesetting — a group's template sets them and a
    line overrides them back. Refusing the tag rather than the effect would refuse those tracks for
    a command that changes no pixel."""
    assert rewrite_ass_event(annotated(raw, (0, 1)), {0: 0x010203}, CATALOG) is not None


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_the_blur_refusal_is_measured_not_assumed() -> None:
    """Why blur is refused, from libass rather than from reasoning about it.

    The first guess was that a spread fill breaks the COLOUR keying — it does not: libass reports
    the reserved color exactly, with any alpha in the low byte the hit map shifts out. What it
    breaks is the EXTENT. Here the images grow by half a glyph and two neighbouring words overlap,
    which is a hover landing on the wrong word.
    """
    libasslite = pytest.importorskip("libasslite")
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: D,sans-serif,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
        "1,0,0,7,0,0,0,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    def render(tag: str) -> list[tuple[int, int, int]]:
        event = (
            rf"Dialogue: 0,0:00:01.00,0:00:03.00,D,,0,0,0,,{{\pos(40,40){tag}\1c&H0000FF&}}猫"
            r"{\1c&H00FF00&}犬" + "\n"
        )
        renderer = libasslite.AssRenderer((header + event).encode())
        try:
            result = renderer.render(2_000, (640, 360), (640, 360), pixel_aspect=1.0)
            return [
                (layer.color >> 8, layer.dst_x, layer.dst_x + layer.width)
                for layer in result.layers
                if layer.image_type == 0
            ]
        finally:
            renderer.close()

    sharp = render("")
    blurred = render(r"\blur4")

    assert [color for color, _left, _right in blurred] == [
        color for color, _left, _right in sharp
    ], "blur changed the reported color, which is not the reason it is refused"
    assert sharp[0][2] <= sharp[1][1], "the sharp boxes should not overlap"
    assert blurred[0][2] > blurred[1][1], "blur is refused because the boxes overlap"


def test_hit_map_requires_exact_unique_non_reserved_colors() -> None:
    source = annotated("猫犬", (0, 1), (1, 2))
    with pytest.raises(ValueError, match="cover every annotation"):
        rewrite_ass_event(source, {0: 1}, CATALOG, require_unique=True)
    with pytest.raises(ValueError, match="must be unique"):
        rewrite_ass_event(source, {0: 1, 1: 1}, CATALOG, require_unique=True)
    with pytest.raises(ValueError, match="reserved"):
        rewrite_ass_event(source, {0: 1, 1: 2}, CATALOG, require_unique=True, reserved_colors=(2,))


def test_palette_identity_includes_event_source_order() -> None:
    first = annotated("猫", (0, 1), source_order=0)
    repeated = annotated("猫", (0, 1), source_order=1)
    palette = allocate_token_colors((first, repeated), reserved_colors=(1,))
    assert tuple(
        (entry.event_id.source_order, entry.token_index, entry.bgr) for entry in palette
    ) == ((0, 0, 2), (1, 0, 3))


def test_palette_rejects_repeated_identity_and_exhaustion() -> None:
    source = annotated("猫犬", (0, 1), (1, 2))
    with pytest.raises(ValueError, match="identity is repeated"):
        allocate_token_colors((source, source))
    with pytest.raises(ValueError, match="exhausted"):
        allocate_token_colors((source,), maximum_color=2, reserved_colors=(1,))
