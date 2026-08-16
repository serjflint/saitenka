from __future__ import annotations

import pytest

from saitenka.subtitles import (
    SubtitleTrackId,
    TokenAnnotation,
    UnsupportedAssEvent,
    authored_ass_rows_at,
    decode_ass_event,
    parse_ass_event_line,
    prepare_ass_hit_map,
    prepare_ass_hit_map_frame,
)
from saitenka.subtitles.ass_geometry import (
    MAX_ACTIVE_EVENTS,
    MAX_ACTIVE_ROW_BYTES,
    MAX_ASS_SOURCE_BYTES,
)
from saitenka.subtitles.geometry import MAX_GEOMETRY_TOKENS

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,犬も見る
""".encode()


def test_prepare_hit_map_rewrites_only_matching_event() -> None:
    prepared = prepare_ass_hit_map(
        ASS,
        SubtitleTrackId("track"),
        start_ms=1_000,
        end_ms=3_000,
        text="猫を見る",
        tokens=(TokenAnnotation(0, 0, 1), TokenAnnotation(1, 2, 4)),
    )

    source_lines = ASS.decode().splitlines()
    result_lines = prepared.ass.decode().splitlines()
    assert result_lines[:-2] == source_lines[:-2]
    assert result_lines[-1] == source_lines[-1]
    assert "\\1c&H" in result_lines[-2]
    assert [item.token_index for item in prepared.palette] == [0, 1]
    assert 0xFFFFFF in prepared.reserved_rgb
    rewritten = parse_ass_event_line(result_lines[-2], SubtitleTrackId("track"), 0)
    assert decode_ass_event(rewritten).text == "猫を見る"


def test_prepare_hit_map_rejects_ambiguous_event_identity() -> None:
    duplicate = ASS + ASS.decode().splitlines()[-2].encode() + b"\n"

    with pytest.raises(UnsupportedAssEvent, match="found 2"):
        prepare_ass_hit_map(
            duplicate,
            SubtitleTrackId("track"),
            start_ms=1_000,
            end_ms=3_000,
            text="猫を見る",
            tokens=(TokenAnnotation(0, 0, 1),),
        )


def test_prepare_hit_map_rejects_token_span_outside_semantic_text() -> None:
    with pytest.raises(ValueError, match="extends beyond"):
        prepare_ass_hit_map(
            ASS,
            SubtitleTrackId("track"),
            start_ms=1_000,
            end_ms=3_000,
            text="猫を見る",
            tokens=(TokenAnnotation(0, 0, 99),),
        )


def test_prepare_frame_matches_and_rewrites_simultaneous_events() -> None:
    overlapping = ASS.replace(
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,犬も見る".encode(),
        "Dialogue: 1,0:00:01.50,0:00:02.50,Default,sign,12,34,56,,犬".encode(),
    )
    active_rows = (
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0000,0000,0000,,猫を見る\n"
        "Dialogue: 1,0:00:01.50,0:00:02.50,Default,sign,0012,0034,0056,,犬"
    )

    prepared = prepare_ass_hit_map_frame(
        overlapping,
        SubtitleTrackId("track"),
        active_rows=active_rows,
        text="猫を見る\n犬",
        tokens=(TokenAnnotation(0, 0, 1), TokenAnnotation(2, 5, 6)),
    )

    assert [item.decoded.source.identity.source_order for item in prepared.events] == [0, 1]
    assert [(item.event_id.source_order, item.token_index) for item in prepared.palette] == [
        (0, 0),
        (1, 2),
    ]
    assert prepared.ass.count(b"\\1c&H") >= 2


def test_prepare_frame_rejects_unmatched_active_event() -> None:
    with pytest.raises(UnsupportedAssEvent, match="does not match"):
        prepare_ass_hit_map_frame(
            ASS,
            SubtitleTrackId("track"),
            active_rows="Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,犬",
            text="犬",
            tokens=(TokenAnnotation(0, 0, 1),),
        )


def test_prepare_frame_rejects_source_over_byte_limit_before_parsing() -> None:
    with pytest.raises(UnsupportedAssEvent, match="subtitle-source-too-large"):
        prepare_ass_hit_map_frame(
            b"x" * (MAX_ASS_SOURCE_BYTES + 1),
            SubtitleTrackId("track"),
            active_rows="",
            text="",
            tokens=(),
        )


def test_prepare_frame_rejects_token_budget_before_source_parse() -> None:
    annotations = tuple(TokenAnnotation(index, 0, 1) for index in range(MAX_GEOMETRY_TOKENS + 1))

    with pytest.raises(ValueError, match="palette entry limit"):
        prepare_ass_hit_map_frame(
            b"not an ASS document",
            SubtitleTrackId("track"),
            active_rows="",
            text="",
            tokens=annotations,
        )


def test_prepare_frame_rejects_token_crossing_event_boundary() -> None:
    overlapping = ASS.replace(b"0:00:04.00,0:00:06.00", b"0:00:01.50,0:00:02.50")
    active_rows = "\n".join(
        line for line in overlapping.decode().splitlines() if line.startswith("Dialogue:")
    )
    with pytest.raises(ValueError, match="crosses"):
        prepare_ass_hit_map_frame(
            overlapping,
            SubtitleTrackId("track"),
            active_rows=active_rows,
            text="猫を見る\n犬も見る",
            tokens=(TokenAnnotation(0, 3, 6),),
        )


def test_authored_frame_accepts_active_event_limit_and_rejects_one_more() -> None:
    header = ASS.decode().split("Dialogue:", 1)[0]
    row = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫\n"

    def source_with(count: int) -> bytes:
        return (header + row * count).encode()

    track = SubtitleTrackId("track")
    rows, text = authored_ass_rows_at(source_with(MAX_ACTIVE_EVENTS), track, 1_500)
    assert len(rows.splitlines()) == MAX_ACTIVE_EVENTS
    assert len(text.splitlines()) == MAX_ACTIVE_EVENTS

    with pytest.raises(UnsupportedAssEvent, match="event limit"):
        authored_ass_rows_at(source_with(MAX_ACTIVE_EVENTS + 1), track, 1_500)


def test_prepare_frame_accepts_active_row_byte_limit_and_rejects_one_more() -> None:
    header = ASS.decode().split("Dialogue:", 1)[0]
    prefix = "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
    text = "a" * (MAX_ACTIVE_ROW_BYTES - len(prefix.encode()))
    row = prefix + text
    source = (header + row + "\n").encode()

    prepared = prepare_ass_hit_map_frame(
        source,
        SubtitleTrackId("track"),
        active_rows=row,
        text=text,
        tokens=(),
    )
    assert prepared.semantic_text == text

    with pytest.raises(UnsupportedAssEvent, match="byte limit"):
        prepare_ass_hit_map_frame(
            source,
            SubtitleTrackId("track"),
            active_rows=row + "a",
            text=text + "a",
            tokens=(),
        )
