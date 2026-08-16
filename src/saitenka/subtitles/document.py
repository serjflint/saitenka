"""Immutable authored-subtitle values, separate from the plain cue projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import NewType

SubtitleTrackId = NewType("SubtitleTrackId", str)


@dataclass(frozen=True, slots=True)
class SubtitleEventId:
    track_id: SubtitleTrackId
    start_ms: int
    end_ms: int
    layer: int
    source_order: int

    def __post_init__(self) -> None:
        values = (self.start_ms, self.end_ms, self.layer, self.source_order)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("subtitle event identity fields must be integers")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("subtitle event time range must be non-empty")
        if self.source_order < 0:
            raise ValueError("subtitle source order must be non-negative")


@dataclass(frozen=True, slots=True)
class RawSubtitleEvent:
    identity: SubtitleEventId
    raw_text: str
    style: str = "Default"
    actor: str = ""
    effect: str = ""
    margins: tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True, slots=True)
class SubtitleAttachment:
    name: str
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class RawSubtitleTrack:
    identity: SubtitleTrackId
    codec: str
    extradata: bytes
    events: tuple[RawSubtitleEvent, ...]
    attachments: tuple[SubtitleAttachment, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class RawTextSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError("raw text span must be non-empty")


@dataclass(frozen=True, slots=True, order=True)
class DrawingSpan:
    start: int
    end: int
    scale: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end or self.scale <= 0:
            raise ValueError("drawing span must be non-empty with a positive scale")


def _validate_raw_offsets(source: RawSubtitleEvent, text: str, offsets: tuple[int, ...]) -> None:
    if len(offsets) != len(text) + 1:
        raise ValueError("raw_offsets must contain one boundary per decoded character")
    if any(left > right for left, right in pairwise(offsets)):
        raise ValueError("raw_offsets must be monotonic")
    if offsets and (offsets[0] < 0 or offsets[-1] > len(source.raw_text)):
        raise ValueError("raw_offsets must refer to source text")


def _validate_exact_spans(
    source: RawSubtitleEvent,
    text: str,
    offsets: tuple[int, ...],
    raw_spans: tuple[RawTextSpan, ...] | None,
    drawings: tuple[DrawingSpan, ...],
) -> None:
    if raw_spans is None:
        if drawings:
            raise ValueError("drawing spans require exact raw text spans")
        return
    if len(raw_spans) != len(text):
        raise ValueError("raw_spans must contain one span per decoded character")
    if any(left.end > right.start for left, right in pairwise(raw_spans)):
        raise ValueError("raw text spans must be ordered and non-overlapping")
    projected = (raw_spans[0].start, *(span.end for span in raw_spans)) if raw_spans else offsets
    if projected != offsets:
        raise ValueError("raw_offsets must project the exact raw text spans")
    if any(left.end > right.start for left, right in pairwise(drawings)):
        raise ValueError("drawing spans must be ordered and non-overlapping")
    spans: tuple[RawTextSpan | DrawingSpan, ...] = tuple(
        sorted((*raw_spans, *drawings), key=lambda span: span.start)
    )
    if any(span.end > len(source.raw_text) for span in spans):
        raise ValueError("raw spans must refer to source text")
    if any(left.end > right.start for left, right in pairwise(spans)):
        raise ValueError("raw spans must be ordered and non-overlapping")


@dataclass(frozen=True, slots=True)
class DecodedSubtitleEvent:
    source: RawSubtitleEvent
    text: str
    raw_offsets: tuple[int, ...]
    drawings: tuple[DrawingSpan, ...] = ()
    raw_spans: tuple[RawTextSpan, ...] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _validate_exact_spans(
            self.source, self.text, self.raw_offsets, self.raw_spans, self.drawings
        )
        _validate_raw_offsets(self.source, self.text, self.raw_offsets)


@dataclass(frozen=True, slots=True)
class TokenAnnotation:
    token_index: int
    text_start: int
    text_end: int

    def __post_init__(self) -> None:
        values = (self.token_index, self.text_start, self.text_end)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("token annotation fields must be integers")
        if self.token_index < 0 or not 0 <= self.text_start < self.text_end:
            raise ValueError("token annotation must identify a non-empty decoded span")


@dataclass(frozen=True, slots=True)
class AnnotatedSubtitleEvent:
    decoded: DecodedSubtitleEvent
    tokens: tuple[TokenAnnotation, ...]

    def __post_init__(self) -> None:
        if any(token.text_end > len(self.decoded.text) for token in self.tokens):
            raise ValueError("token annotation extends beyond decoded text")
        if len({token.token_index for token in self.tokens}) != len(self.tokens):
            raise ValueError("token indices must be unique within an event")
        if any(
            left.text_end > right.text_start
            for left, right in zip(self.tokens, self.tokens[1:], strict=False)
        ):
            raise ValueError("token annotations must be ordered and non-overlapping")
