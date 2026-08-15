from __future__ import annotations

import pytest

from saitenka.subtitles import (
    AnnotatedSubtitleEvent,
    DecodedSubtitleEvent,
    RawSubtitleEvent,
    SubtitleEventId,
    SubtitleTrackId,
    TokenAnnotation,
)


def event(raw_text: str = r"{\i1}猫{\i0}") -> RawSubtitleEvent:
    track_id = SubtitleTrackId("external:/tmp/example.ass:1")
    return RawSubtitleEvent(SubtitleEventId(track_id, 1_000, 2_000, 0, 3), raw_text)


def test_decoded_event_retains_raw_offset_map() -> None:
    decoded = DecodedSubtitleEvent(event(), "猫", (5, 6))
    annotated = AnnotatedSubtitleEvent(decoded, (TokenAnnotation(0, 0, 1),))

    token = annotated.tokens[0]
    assert (
        decoded.source.raw_text[
            decoded.raw_offsets[token.text_start] : decoded.raw_offsets[token.text_end]
        ]
        == "猫"
    )


@pytest.mark.parametrize("offsets", [(5,), (6, 5), (-1, 6), (5, 99)])
def test_decoded_event_rejects_invalid_raw_offset_map(offsets: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        DecodedSubtitleEvent(event(), "猫", offsets)


def test_annotation_rejects_span_beyond_decoded_text() -> None:
    decoded = DecodedSubtitleEvent(event(), "猫", (5, 6))

    with pytest.raises(ValueError):
        AnnotatedSubtitleEvent(decoded, (TokenAnnotation(0, 0, 2),))
