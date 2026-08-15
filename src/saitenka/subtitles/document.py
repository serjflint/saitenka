"""Immutable authored-subtitle values, separate from the plain cue projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

SubtitleTrackId = NewType("SubtitleTrackId", str)


@dataclass(frozen=True, slots=True)
class SubtitleEventId:
    track_id: SubtitleTrackId
    start_ms: int
    end_ms: int
    layer: int
    source_order: int


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


@dataclass(frozen=True, slots=True)
class DecodedSubtitleEvent:
    source: RawSubtitleEvent
    text: str
    raw_offsets: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.raw_offsets) != len(self.text) + 1:
            raise ValueError("raw_offsets must contain one boundary per decoded character")
        if any(a > b for a, b in zip(self.raw_offsets, self.raw_offsets[1:], strict=False)):
            raise ValueError("raw_offsets must be monotonic")
        if self.raw_offsets and (
            self.raw_offsets[0] < 0 or self.raw_offsets[-1] > len(self.source.raw_text)
        ):
            raise ValueError("raw_offsets must refer to source text")


@dataclass(frozen=True, slots=True)
class TokenAnnotation:
    token_index: int
    text_start: int
    text_end: int

    def __post_init__(self) -> None:
        if self.token_index < 0 or not 0 <= self.text_start < self.text_end:
            raise ValueError("token annotation must identify a non-empty decoded span")


@dataclass(frozen=True, slots=True)
class AnnotatedSubtitleEvent:
    decoded: DecodedSubtitleEvent
    tokens: tuple[TokenAnnotation, ...]

    def __post_init__(self) -> None:
        if any(token.text_end > len(self.decoded.text) for token in self.tokens):
            raise ValueError("token annotation extends beyond decoded text")
