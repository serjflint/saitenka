from __future__ import annotations

import pytest
from saitenka_subtitles import (
    AnnotatedSubtitleEvent,
    DecodedSubtitleEvent,
    RawSubtitleEvent,
    RawTextSpan,
    SubtitleEventId,
    SubtitleTrackId,
    TokenAnnotation,
)


def event(raw_text: str = r"{\i1}猫{\i0}") -> RawSubtitleEvent:
    track_id = SubtitleTrackId("external:/tmp/example.ass:1")
    return RawSubtitleEvent(SubtitleEventId(track_id, 1_000, 2_000, 0, 3), raw_text)


def test_decoded_event_retains_raw_offset_map() -> None:
    decoded = DecodedSubtitleEvent(event(), "猫", (5, 6), raw_spans=(RawTextSpan(5, 6),))
    annotated = AnnotatedSubtitleEvent(decoded, (TokenAnnotation(0, 0, 1),))

    token = annotated.tokens[0]
    assert (
        decoded.source.raw_text[
            decoded.raw_offsets[token.text_start] : decoded.raw_offsets[token.text_end]
        ]
        == "猫"
    )


def test_decoded_event_keeps_keyword_raw_offsets_constructor() -> None:
    decoded = DecodedSubtitleEvent(source=event(), text="猫", raw_offsets=(5, 6))
    assert decoded.raw_offsets == (5, 6)
    assert decoded.raw_spans is None


@pytest.mark.parametrize(
    "spans",
    [(), (RawTextSpan(5, 6), RawTextSpan(6, 7)), (RawTextSpan(5, 99),)],
)
def test_decoded_event_rejects_invalid_raw_spans(spans: tuple[RawTextSpan, ...]) -> None:
    with pytest.raises(ValueError):
        DecodedSubtitleEvent(event(), "猫", (5, 6), raw_spans=spans)


def test_decoded_event_rejects_reversed_raw_spans() -> None:
    with pytest.raises(ValueError, match="raw text spans must be ordered"):
        DecodedSubtitleEvent(
            event("猫犬"),
            "猫犬",
            (1, 2, 1),
            raw_spans=(RawTextSpan(1, 2), RawTextSpan(0, 1)),
        )


def test_annotation_rejects_span_beyond_decoded_text() -> None:
    decoded = DecodedSubtitleEvent(event(), "猫", (5, 6), raw_spans=(RawTextSpan(5, 6),))

    with pytest.raises(ValueError):
        AnnotatedSubtitleEvent(decoded, (TokenAnnotation(0, 0, 2),))


def test_annotations_reject_overlap_and_repeated_identity() -> None:
    decoded = DecodedSubtitleEvent(
        event("猫を見る"),
        "猫を見る",
        (0, 1, 2, 3, 4),
        raw_spans=tuple(RawTextSpan(index, index + 1) for index in range(4)),
    )

    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        AnnotatedSubtitleEvent(
            decoded,
            (TokenAnnotation(0, 0, 2), TokenAnnotation(1, 1, 4)),
        )
    with pytest.raises(ValueError, match="indices must be unique"):
        AnnotatedSubtitleEvent(
            decoded,
            (TokenAnnotation(0, 0, 1), TokenAnnotation(0, 2, 4)),
        )


def test_event_identity_accepts_valid_values() -> None:
    identity = SubtitleEventId(SubtitleTrackId("track"), 1_000, 2_000, -1, 0)

    assert identity.end_ms > identity.start_ms


@pytest.mark.parametrize(
    "values",
    [(2_000, 1_000, 0, 0), (1_000, 1_000, 0, 0), (1_000, 2_000, 0, -1)],
)
def test_event_identity_rejects_invalid_ranges(values: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError):
        SubtitleEventId(SubtitleTrackId("track"), *values)
