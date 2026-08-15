"""Pure subtitle parsing and cue navigation."""

from saitenka.subtitles.document import (
    AnnotatedSubtitleEvent,
    DecodedSubtitleEvent,
    RawSubtitleEvent,
    RawSubtitleTrack,
    SubtitleAttachment,
    SubtitleEventId,
    SubtitleTrackId,
    TokenAnnotation,
)
from saitenka.subtitles.geometry import (
    GeometryBackend,
    GeometryRequest,
    GeometrySnapshot,
    GeometryVariant,
    Rect,
    TokenGeometry,
)
from saitenka.subtitles.index import CueIndex
from saitenka.subtitles.model import Cue
from saitenka.subtitles.parsers import parse_ass, parse_cues, parse_srt

__all__ = [
    "AnnotatedSubtitleEvent",
    "Cue",
    "CueIndex",
    "DecodedSubtitleEvent",
    "GeometryBackend",
    "GeometryRequest",
    "GeometrySnapshot",
    "GeometryVariant",
    "RawSubtitleEvent",
    "RawSubtitleTrack",
    "Rect",
    "SubtitleAttachment",
    "SubtitleEventId",
    "SubtitleTrackId",
    "TokenAnnotation",
    "TokenGeometry",
    "parse_ass",
    "parse_cues",
    "parse_srt",
]
