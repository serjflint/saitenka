from __future__ import annotations

import pytest

from saitenka.subtitles import (
    SubtitleTrackId,
    TokenAnnotation,
    UnsupportedAssEvent,
    decode_ass_event,
    parse_ass_event_line,
    prepare_ass_hit_map,
)

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
