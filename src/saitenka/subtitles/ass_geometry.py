"""Prepare one authored ASS event for hidden libass hit-map rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.subtitles.ass import (
    UnsupportedAssEvent,
    allocate_token_colors,
    decode_ass_event,
    parse_ass_event_line,
    parse_ass_styles,
    rewrite_ass_event,
    serialize_ass_event_line,
    source_primary_bgr_colors,
)
from saitenka.subtitles.document import AnnotatedSubtitleEvent, RawSubtitleEvent
from saitenka.subtitles.geometry import GeometryPaletteEntry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka.subtitles.document import SubtitleTrackId, TokenAnnotation


@dataclass(frozen=True, slots=True)
class PreparedAssGeometry:
    ass: bytes
    event: AnnotatedSubtitleEvent
    palette: tuple[GeometryPaletteEntry, ...]
    reserved_rgb: tuple[int, ...]


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


def prepare_ass_hit_map(
    source: bytes,
    track_id: SubtitleTrackId,
    *,
    start_ms: int,
    end_ms: int,
    text: str,
    tokens: Sequence[TokenAnnotation],
) -> PreparedAssGeometry:
    """Rewrite the uniquely matching event and preserve every other document byte."""
    has_bom = source.startswith(b"\xef\xbb\xbf")
    try:
        decoded_source = source.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnsupportedAssEvent("subtitle-source-encoding-unsupported") from error
    lines, indexed_events = _authored_events(decoded_source, track_id)
    decoded_events = tuple((index, decode_ass_event(event)) for index, event in indexed_events)
    normalized = text.replace("\r", "").replace("\\N", "\n")
    matches = [
        (index, event)
        for index, event in decoded_events
        if event.source.identity.start_ms == start_ms
        and event.source.identity.end_ms == end_ms
        and event.text == normalized
    ]
    if len(matches) != 1:
        raise UnsupportedAssEvent(f"expected one authored ASS event, found {len(matches)}")
    line_index, decoded = matches[0]
    annotated = AnnotatedSubtitleEvent(decoded, tuple(tokens))
    catalog = parse_ass_styles(source)
    raw_events = tuple(event for _index, event in indexed_events)
    reserved_bgr = source_primary_bgr_colors(catalog, raw_events)
    colors = allocate_token_colors((annotated,), reserved_colors=reserved_bgr)
    by_index = {item.token_index: item.bgr for item in colors}
    rewritten = rewrite_ass_event(
        annotated,
        by_index,
        catalog,
        require_unique=True,
        reserved_colors=reserved_bgr,
    )
    if lines[line_index].endswith("\r\n"):
        ending = "\r\n"
    elif lines[line_index].endswith("\n"):
        ending = "\n"
    else:
        ending = ""
    lines[line_index] = serialize_ass_event_line(rewritten.event) + ending
    encoded = "".join(lines).encode("utf-8")
    if has_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    return PreparedAssGeometry(
        encoded,
        annotated,
        tuple(
            GeometryPaletteEntry(item.event_id, item.token_index, _bgr_to_rgb(item.bgr))
            for item in colors
        ),
        tuple(_bgr_to_rgb(color) for color in reserved_bgr),
    )
