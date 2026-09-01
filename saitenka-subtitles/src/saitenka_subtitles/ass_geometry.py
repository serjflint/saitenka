"""Prepare authored ASS frames for hidden libass hit-map rendering."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import TYPE_CHECKING

from saitenka_subtitles.ass import (
    AssStyleCatalog,
    UnsupportedAssEvent,
    allocate_token_colors,
    decode_ass_event,
    parse_ass_event_line,
    parse_ass_styles,
    rewrite_ass_event,
    serialize_ass_event_line,
    source_primary_bgr_colors,
)
from saitenka_subtitles.document import (
    AnnotatedSubtitleEvent,
    RawSubtitleEvent,
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
    TokenAnnotation,
)
from saitenka_subtitles.geometry import MAX_GEOMETRY_TOKENS, GeometryPaletteEntry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from saitenka_subtitles.document import DecodedSubtitleEvent

MAX_ACTIVE_EVENTS = 64
MAX_ACTIVE_ROW_BYTES = 1_048_576
MAX_ASS_SOURCE_BYTES = 8 * 1_048_576


@dataclass(frozen=True, slots=True)
class PreparedAssGeometry:
    """Compatibility value for callers that have already proved one active event."""

    ass: bytes
    event: AnnotatedSubtitleEvent
    palette: tuple[GeometryPaletteEntry, ...]
    reserved_rgb: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PreparedAssFrame:
    ass: bytes
    frame_id: SubtitleFrameId
    events: tuple[AnnotatedSubtitleEvent, ...]
    semantic_text: str
    palette: tuple[GeometryPaletteEntry, ...]
    reserved_rgb: tuple[int, ...]
    #: The document's script height, which is what libass scales its font sizes from. Carried so a
    #: caller that knows the frame can turn a style's script-unit size into frame pixels; `0` when
    #: the document declares none, which is the case where nothing may be drawn over the cue.
    #: Required, not defaulted: a default is what let this field ship reading zero for every cue,
    #: which silently switched the overprint off everywhere rather than failing anywhere.
    play_res_y: int


@dataclass(frozen=True, slots=True)
class _ParsedAssSource:
    lines: tuple[str, ...]
    indexed_events: tuple[tuple[int, RawSubtitleEvent], ...]
    catalog: AssStyleCatalog
    signature_index: Mapping[tuple[object, ...], tuple[RawSubtitleEvent, ...]]
    reserved_bgr: tuple[int, ...]
    has_bom: bool
    play_res_y: int


def _token_faces(
    events: Sequence[AnnotatedSubtitleEvent], catalog: AssStyleCatalog
) -> dict[SubtitleEventId, tuple[str, float]]:
    """The face and script-unit size each active event's style lays its text out in.

    Per event rather than per token because the style is an event's, and an override tag that
    changed the face mid-event would make the event unpaintable rather than differently painted —
    such a cue is refused upstream, so a per-event answer is the whole answer here.
    """
    by_name = {style.name: style for style in catalog.styles}
    faces: dict[SubtitleEventId, tuple[str, float]] = {}
    for event in events:
        source = event.decoded.source
        style = by_name.get(source.style)
        faces[source.identity] = ("", 0.0) if style is None else (style.font_name, style.font_size)
    return faces


def _bgr_to_rgb(color: int) -> int:
    return ((color & 0xFF) << 16) | (color & 0x00FF00) | ((color >> 16) & 0xFF)


def _section_state(line: str) -> bool | None:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped.casefold() == "[events]"
    return None


def _authored_row(
    line: str,
    track_id: SubtitleTrackId,
    source_order: int,
    fields: tuple[str, ...] | None,
) -> tuple[tuple[str, ...] | None, RawSubtitleEvent | None]:
    key, separator, value = line.partition(":")
    if not separator:
        return fields, None
    row_kind = key.strip()
    if row_kind == "Format":
        return tuple(item.strip() for item in value.split(",")), None
    if row_kind != "Dialogue":
        return fields, None
    if fields is None:
        raise UnsupportedAssEvent("ASS Dialogue appears before its Format row")
    return fields, parse_ass_event_line(line.lstrip(), track_id, source_order, fields=fields)


def _authored_events(
    text: str,
    track_id: SubtitleTrackId,
) -> tuple[list[str], tuple[tuple[int, RawSubtitleEvent], ...]]:
    lines = text.splitlines(keepends=True)
    in_events = False
    fields: tuple[str, ...] | None = None
    parsed: list[tuple[int, RawSubtitleEvent]] = []
    source_order = 0
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        section_state = _section_state(line)
        if section_state is not None:
            in_events = section_state
            fields = None
            continue
        if not in_events:
            continue
        fields, event = _authored_row(line, track_id, source_order, fields)
        if event is None:
            continue
        parsed.append((index, event))
        source_order += 1
    if not parsed:
        raise UnsupportedAssEvent("ASS document has no interactive Dialogue events")
    return lines, tuple(parsed)


@lru_cache(maxsize=2)
def _parsed_source(source: bytes, track_id: SubtitleTrackId) -> _ParsedAssSource:
    if len(source) > MAX_ASS_SOURCE_BYTES:
        raise UnsupportedAssEvent("subtitle-source-too-large")
    try:
        decoded_source = source.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnsupportedAssEvent("subtitle-source-encoding-unsupported") from error
    lines, indexed_events = _authored_events(decoded_source, track_id)
    catalog = parse_ass_styles(source)
    raw_events = tuple(event for _index, event in indexed_events)
    indexed_by_signature: dict[tuple[object, ...], list[RawSubtitleEvent]] = {}
    for event in raw_events:
        indexed_by_signature.setdefault(_event_signature(event), []).append(event)
    return _ParsedAssSource(
        tuple(lines),
        indexed_events,
        catalog,
        MappingProxyType(
            {signature: tuple(events) for signature, events in indexed_by_signature.items()}
        ),
        source_primary_bgr_colors(catalog, raw_events),
        source.startswith(b"\xef\xbb\xbf"),
        _play_res_y(decoded_source),
    )


def _play_res_y(document: str) -> int:
    """The `[Script Info]` `PlayResY`, or 0 when the document declares none.

    Zero rather than libass's own default: this value only feeds the overprint, and guessing it
    would put a colored glyph at the wrong size over the right word.
    """
    for line in document.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip().casefold() == "playresy":
            try:
                return int(float(value.strip()))
            except ValueError:
                return 0
    return 0


def _event_signature(event: RawSubtitleEvent) -> tuple[object, ...]:
    identity = event.identity
    return (
        identity.start_ms,
        identity.end_ms,
        identity.layer,
        event.style,
        event.actor,
        event.effect,
        event.margins,
        event.raw_text,
    )


def canonical_active_ass_rows(active_rows: str) -> str:
    """Normalize mpv/source formatting without weakening the full event metadata contract."""
    track_id = SubtitleTrackId("canonical")
    return "\n".join(
        serialize_ass_event_line(parse_ass_event_line(row, track_id, order))
        for order, row in enumerate(active_rows.splitlines())
        if row
    )


def _match_active_events(
    active_rows: str,
    track_id: SubtitleTrackId,
    signature_index: Mapping[tuple[object, ...], tuple[RawSubtitleEvent, ...]],
) -> tuple[DecodedSubtitleEvent, ...]:
    encoded_size = len(active_rows.encode("utf-8"))
    if encoded_size > MAX_ACTIVE_ROW_BYTES:
        raise UnsupportedAssEvent("active ASS row byte limit exceeded")
    rows = tuple(row for row in active_rows.splitlines() if row)
    if not rows:
        raise UnsupportedAssEvent("mpv reported no active ASS event rows")
    if len(rows) > MAX_ACTIVE_EVENTS:
        raise UnsupportedAssEvent("active ASS event limit exceeded")
    used: set[int] = set()
    matched: list[DecodedSubtitleEvent] = []
    for order, row in enumerate(rows):
        active = parse_ass_event_line(row, track_id, order)
        signature = _event_signature(active)
        candidates = [
            event
            for event in signature_index.get(signature, ())
            if event.identity.source_order not in used
        ]
        if not candidates:
            raise UnsupportedAssEvent("active ASS event does not match the authored source")
        selected = min(candidates, key=lambda event: event.identity.source_order)
        used.add(selected.identity.source_order)
        matched.append(decode_ass_event(selected))
    return tuple(matched)


def _partition_tokens(
    events: tuple[DecodedSubtitleEvent, ...],
    tokens: Sequence[TokenAnnotation],
) -> tuple[AnnotatedSubtitleEvent, ...]:
    remaining = list(tokens)
    annotated: list[AnnotatedSubtitleEvent] = []
    offset = 0
    for event in events:
        end = offset + len(event.text)
        local: list[TokenAnnotation] = []
        while remaining and remaining[0].text_start < end:
            token = remaining.pop(0)
            if token.text_start < offset or token.text_end > end:
                raise ValueError("token annotation extends beyond or crosses an active ASS event")
            local.append(
                TokenAnnotation(
                    token.token_index,
                    token.text_start - offset,
                    token.text_end - offset,
                )
            )
        annotated.append(AnnotatedSubtitleEvent(event, tuple(local)))
        offset = end + 1
    if remaining:
        raise ValueError("token annotation extends beyond active ASS events")
    return tuple(annotated)


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    return "\n" if line.endswith("\n") else ""


def authored_ass_rows_at(
    source: bytes,
    track_id: SubtitleTrackId,
    timestamp_ms: int,
) -> tuple[str, str]:
    """Canonical active rows and semantic projection for instant navigation before mpv catches up."""
    indexed_events = _parsed_source(source, track_id).indexed_events
    active = tuple(
        event
        for _index, event in indexed_events
        if event.identity.start_ms <= timestamp_ms < event.identity.end_ms
    )
    if not active:
        raise UnsupportedAssEvent("authored ASS has no event at the requested timestamp")
    if len(active) > MAX_ACTIVE_EVENTS:
        raise UnsupportedAssEvent("active ASS event limit exceeded")
    rows = "\n".join(serialize_ass_event_line(event) for event in active)
    if len(rows.encode("utf-8")) > MAX_ACTIVE_ROW_BYTES:
        raise UnsupportedAssEvent("active ASS row byte limit exceeded")
    return rows, "\n".join(decode_ass_event(event).text for event in active)


def prepare_ass_hit_map_frame(
    source: bytes,
    track_id: SubtitleTrackId,
    *,
    active_rows: str,
    text: str,
    tokens: Sequence[TokenAnnotation],
) -> PreparedAssFrame:
    """Rewrite every active authored event reported by mpv and preserve the rest of the document."""
    if len(tokens) > MAX_GEOMETRY_TOKENS:
        raise ValueError("geometry palette entry limit exceeded")
    parsed = _parsed_source(source, track_id)
    lines = list(parsed.lines)
    indexed_events = parsed.indexed_events
    decoded_events = _match_active_events(active_rows, track_id, parsed.signature_index)
    semantic_text = "\n".join(event.text for event in decoded_events)
    normalized = text.replace("\r", "").replace("\\N", "\n")
    if semantic_text != normalized:
        raise UnsupportedAssEvent("active ASS semantic projection does not match mpv sub-text")
    annotated = _partition_tokens(decoded_events, tokens)
    reserved_bgr = parsed.reserved_bgr
    colors = allocate_token_colors(annotated, reserved_colors=reserved_bgr)
    colors_by_event: dict[SubtitleEventId, dict[int, int]] = {}
    for color in colors:
        colors_by_event.setdefault(color.event_id, {})[color.token_index] = color.bgr
    line_by_source_order = {event.identity.source_order: index for index, event in indexed_events}
    for event in annotated:
        by_index = colors_by_event.get(event.decoded.source.identity, {})
        rewritten = rewrite_ass_event(
            event,
            by_index,
            parsed.catalog,
            require_unique=True,
            reserved_colors=reserved_bgr,
        )
        line_index = line_by_source_order[event.decoded.source.identity.source_order]
        lines[line_index] = serialize_ass_event_line(rewritten.event) + _line_ending(
            lines[line_index]
        )
    encoded = "".join(lines).encode("utf-8")
    if parsed.has_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    frame_id = SubtitleFrameId(
        track_id, tuple(event.decoded.source.identity for event in annotated)
    )
    faces = _token_faces(annotated, parsed.catalog)
    return PreparedAssFrame(
        encoded,
        frame_id,
        annotated,
        semantic_text,
        tuple(
            GeometryPaletteEntry(
                item.event_id,
                item.token_index,
                _bgr_to_rgb(item.bgr),
                *faces.get(item.event_id, ("", 0.0)),
            )
            for item in colors
        ),
        tuple(_bgr_to_rgb(color) for color in reserved_bgr),
        parsed.play_res_y,
    )


def prepare_ass_hit_map(
    source: bytes,
    track_id: SubtitleTrackId,
    *,
    start_ms: int,
    end_ms: int,
    text: str,
    tokens: Sequence[TokenAnnotation],
) -> PreparedAssGeometry:
    """Compatibility wrapper after a caller has proved one uniquely matching authored event."""
    indexed_events = _parsed_source(source, track_id).indexed_events
    normalized = text.replace("\r", "").replace("\\N", "\n")
    decoded_events = (decode_ass_event(event) for _index, event in indexed_events)
    matches = [
        event
        for event in decoded_events
        if event.source.identity.start_ms == start_ms
        and event.source.identity.end_ms == end_ms
        and event.text == normalized
    ]
    if len(matches) != 1:
        raise UnsupportedAssEvent(f"expected one authored ASS event, found {len(matches)}")
    frame = prepare_ass_hit_map_frame(
        source,
        track_id,
        active_rows=serialize_ass_event_line(matches[0].source),
        text=text,
        tokens=tokens,
    )
    return PreparedAssGeometry(frame.ass, frame.events[0], frame.palette, frame.reserved_rgb)
