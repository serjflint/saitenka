"""Pure subtitle parsing and cue navigation."""

from saitenka.subtitles.ass import (
    AssColorRewrite,
    AssStyle,
    AssStyleCatalog,
    TokenColor,
    UnsupportedAssEvent,
    allocate_token_colors,
    decode_ass_event,
    parse_ass_event_line,
    parse_ass_styles,
    rewrite_ass_event,
    serialize_ass_event_line,
)
from saitenka.subtitles.document import (
    AnnotatedSubtitleEvent,
    DecodedSubtitleEvent,
    DrawingSpan,
    RawSubtitleEvent,
    RawSubtitleTrack,
    RawTextSpan,
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
    "AssColorRewrite",
    "AssStyle",
    "AssStyleCatalog",
    "Cue",
    "CueIndex",
    "DecodedSubtitleEvent",
    "DrawingSpan",
    "GeometryBackend",
    "GeometryRequest",
    "GeometrySnapshot",
    "GeometryVariant",
    "RawSubtitleEvent",
    "RawSubtitleTrack",
    "RawTextSpan",
    "Rect",
    "SubtitleAttachment",
    "SubtitleEventId",
    "SubtitleTrackId",
    "TokenAnnotation",
    "TokenColor",
    "TokenGeometry",
    "UnsupportedAssEvent",
    "allocate_token_colors",
    "decode_ass_event",
    "parse_ass",
    "parse_ass_event_line",
    "parse_ass_styles",
    "parse_cues",
    "parse_srt",
    "rewrite_ass_event",
    "serialize_ass_event_line",
]
