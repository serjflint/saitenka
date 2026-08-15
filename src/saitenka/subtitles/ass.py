"""Lossless ASS event decoding and token-color rewriting."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from itertools import pairwise, starmap
from typing import TYPE_CHECKING

from saitenka.subtitles.document import (
    AnnotatedSubtitleEvent,
    DecodedSubtitleEvent,
    DrawingSpan,
    RawSubtitleEvent,
    RawTextSpan,
    SubtitleEventId,
    SubtitleTrackId,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

_DRAWING = re.compile(r"\\p(-?\d+)(?!\d)")
_UNSAFE = re.compile(r"\\(?:t|move|fad|fade|kt|k|K|kf|ko)(?=[(\d\\}]|$)")
_COLOR_STATE = re.compile(
    r"\\(?:(?P<color_command>1?c)"
    r"(?:(?P<color_spec>(?:&H?|)?(?P<color>[0-9A-Fa-f]{1,8})&?|&H&?)"
    r"(?![0-9A-Fa-f])|(?=\\|$))"
    r"|r(?P<style>[^\\}]*))"
)
_PRIMARY_COLOR_COMMAND = re.compile(r"\\1?c")
_ASS_TIME = re.compile(
    r"(?P<hours>\d+):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\.(?P<centiseconds>\d{2})"
)
_EVENT_FIELDS = (
    "layer",
    "start",
    "end",
    "style",
    "name",
    "marginl",
    "marginr",
    "marginv",
    "effect",
    "text",
)
_BIDI_CLASSES = {"R", "AL", "AN", "RLE", "RLO", "RLI", "LRE", "LRO", "LRI", "FSI", "PDI", "PDF"}


class UnsupportedAssEvent(ValueError):
    """The event cannot be rewritten without guessing libass state."""


@dataclass(frozen=True, slots=True)
class AssStyle:
    name: str
    primary_color: str

    def __post_init__(self) -> None:
        if not self.name or not re.fullmatch(r"[0-9A-Fa-f]{1,8}", self.primary_color):
            raise ValueError("ASS style needs a name and a 1-8 digit primary color")


@dataclass(frozen=True, slots=True)
class AssStyleCatalog:
    styles: tuple[AssStyle, ...]

    def __post_init__(self) -> None:
        names = [style.name for style in self.styles]
        if len(names) != len(set(names)):
            raise ValueError("ASS style names must be unique")

    def primary_color(self, name: str) -> str:
        match = next((style for style in self.styles if style.name == name), None)
        if match is None:
            raise UnsupportedAssEvent(f"unknown ASS style: {name}")
        return match.primary_color.upper()


@dataclass(frozen=True, slots=True, order=True)
class TokenColor:
    event_id: SubtitleEventId
    token_index: int
    bgr: int

    def __post_init__(self) -> None:
        if self.token_index < 0 or not 0 < self.bgr <= 0xFFFFFF:
            raise ValueError("token color must have a token index and non-zero 24-bit BGR value")


@dataclass(frozen=True, slots=True)
class ColorInsertion:
    raw_offset: int
    override: str
    after_token: bool


@dataclass(frozen=True, slots=True)
class AssColorRewrite:
    source: RawSubtitleEvent
    event: RawSubtitleEvent
    insertions: tuple[ColorInsertion, ...]

    def restore(self) -> RawSubtitleEvent:
        if self.event.raw_text != _apply_insertions(self.source.raw_text, self.insertions):
            raise ValueError("rewritten ASS event no longer matches its insertion record")
        return self.source


@dataclass(frozen=True, slots=True)
class _OverrideBlock:
    start: int
    end: int
    content: str


@dataclass(slots=True)
class _DecodeState:
    text: list[str]
    spans: list[RawTextSpan]
    drawings: list[DrawingSpan]
    drawing_scale: int = 0
    drawing_start: int | None = None


def _override_at(raw: str, start: int) -> _OverrideBlock | None:
    end = raw.find("}", start + 1)
    if end < 0:
        return None
    return _OverrideBlock(start, end + 1, raw[start + 1 : end])


def _consume_override(state: _DecodeState, block: _OverrideBlock) -> None:
    if state.drawing_start is not None and state.drawing_start < block.start:
        state.drawings.append(DrawingSpan(state.drawing_start, block.start, state.drawing_scale))
        state.drawing_start = None
    for match in _DRAWING.finditer(block.content):
        state.drawing_scale = max(0, int(match.group(1)))
    if state.drawing_scale:
        state.drawing_start = block.end


def _consume_text(raw: str, index: int, state: _DecodeState) -> int:
    if raw[index] == "\\" and index + 1 < len(raw) and raw[index + 1] in "Nnh":
        escaped = raw[index + 1]
        state.text.append("\n" if escaped in "Nn" else " ")
        state.spans.append(RawTextSpan(index, index + 2))
        return index + 2
    state.text.append(raw[index])
    state.spans.append(RawTextSpan(index, index + 1))
    return index + 1


def decode_ass_event(source: RawSubtitleEvent) -> DecodedSubtitleEvent:
    """Project an authored ASS event to semantic text and exact source spans."""
    raw = source.raw_text
    state = _DecodeState([], [], [])
    index = 0
    while index < len(raw):
        block = _override_at(raw, index) if raw[index] == "{" else None
        if block is not None:
            _consume_override(state, block)
            index = block.end
            continue
        if state.drawing_scale:
            index += 1
            continue
        index = _consume_text(raw, index, state)
    if state.drawing_start is not None and state.drawing_start < len(raw):
        state.drawings.append(DrawingSpan(state.drawing_start, len(raw), state.drawing_scale))
    return DecodedSubtitleEvent(
        source,
        "".join(state.text),
        (state.spans[0].start, *(span.end for span in state.spans)) if state.spans else (0,),
        tuple(state.drawings),
        raw_spans=tuple(state.spans),
    )


def _style_rows(text: str) -> Iterable[tuple[tuple[str, ...], str]]:
    in_styles = False
    fields: tuple[str, ...] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_styles = line.casefold() in {"[v4+ styles]", "[v4 styles]"}
            fields = None
            continue
        key, separator, value = line.partition(":")
        if not in_styles or not separator:
            continue
        if key == "Format":
            fields = tuple(item.strip().casefold() for item in value.split(","))
        elif key == "Style" and fields is not None:
            yield fields, value


def _parse_style(fields: Sequence[str], value: str) -> AssStyle:
    values = [item.strip() for item in value.split(",", maxsplit=len(fields) - 1)]
    if len(values) != len(fields):
        raise UnsupportedAssEvent("ASS style row does not match its Format row")
    row = dict(zip(fields, values, strict=True))
    color_match = re.fullmatch(r"&H([0-9A-Fa-f]{1,8})&?", row.get("primarycolour", ""))
    if not color_match or not row.get("name"):
        raise UnsupportedAssEvent("ASS style has no parseable primary color")
    return AssStyle(row["name"], color_match.group(1))


def parse_ass_styles(extradata: bytes) -> AssStyleCatalog:
    """Read the primary colors needed to restore source color after a token."""
    styles = tuple(starmap(_parse_style, _style_rows(extradata.decode("utf-8-sig"))))
    if not styles:
        raise UnsupportedAssEvent("ASS extradata has no styles")
    return AssStyleCatalog(styles)


def _parse_ass_time(value: str) -> int:
    match = _ASS_TIME.fullmatch(value.strip())
    if match is None:
        raise UnsupportedAssEvent(f"invalid ASS timestamp: {value}")
    return (
        int(match.group("hours")) * 3_600_000
        + int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + int(match.group("centiseconds")) * 10
    )


def _format_ass_time(value: int) -> str:
    if value < 0 or value % 10:
        raise ValueError("ASS timestamps must be non-negative whole centiseconds")
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"


def parse_ass_event_line(
    line: str,
    track_id: SubtitleTrackId,
    source_order: int,
    *,
    fields: Sequence[str] = _EVENT_FIELDS,
) -> RawSubtitleEvent:
    """Parse one dialogue row while leaving its authored text byte-for-byte intact."""
    kind, separator, payload = line.partition(":")
    if not separator or kind != "Dialogue":
        raise UnsupportedAssEvent("only ASS Dialogue rows are interactive events")
    normalized_fields = tuple(field.strip().casefold() for field in fields)
    if len(normalized_fields) != len(set(normalized_fields)) or "text" not in normalized_fields:
        raise UnsupportedAssEvent("ASS event Format fields must be unique and include Text")
    if normalized_fields != _EVENT_FIELDS:
        raise UnsupportedAssEvent("only the canonical V4+ event Format is supported")
    if normalized_fields[-1] != "text":
        raise UnsupportedAssEvent("ASS Text must be the final event field")
    values = payload.lstrip().split(",", maxsplit=len(fields) - 1)
    if len(values) != len(fields):
        raise UnsupportedAssEvent("ASS dialogue row does not match its Format row")
    values[:-1] = [value.strip() for value in values[:-1]]
    row = dict(zip(normalized_fields, values, strict=True))
    try:
        layer = int(row["layer"])
        margins = (int(row["marginl"]), int(row["marginr"]), int(row["marginv"]))
    except ValueError as error:
        raise UnsupportedAssEvent("ASS layer and margins must be integers") from error
    identity = SubtitleEventId(
        track_id,
        _parse_ass_time(row["start"]),
        _parse_ass_time(row["end"]),
        layer,
        source_order,
    )
    return RawSubtitleEvent(
        identity,
        row["text"],
        row["style"],
        row.get("name", row.get("actor", "")),
        row["effect"],
        margins,
    )


def serialize_ass_event_line(event: RawSubtitleEvent) -> str:
    """Serialize the canonical V4+ event field order without touching raw text."""
    identity = event.identity
    fields = (
        str(identity.layer),
        _format_ass_time(identity.start_ms),
        _format_ass_time(identity.end_ms),
        event.style,
        event.actor,
        *(str(margin) for margin in event.margins),
        event.effect,
        event.raw_text,
    )
    return "Dialogue: " + ",".join(fields)


def _blocks(raw: str) -> tuple[_OverrideBlock, ...]:
    blocks: list[_OverrideBlock] = []
    index = 0
    while index < len(raw):
        if raw[index] != "{":
            index += 1
            continue
        block = _override_at(raw, index)
        if block is None:
            raise UnsupportedAssEvent("unclosed ASS override block")
        blocks.append(block)
        index = block.end
    return tuple(blocks)


def _effective_color(
    source: RawSubtitleEvent,
    catalog: AssStyleCatalog,
    blocks: Sequence[_OverrideBlock],
    raw_offset: int,
) -> str:
    color = catalog.primary_color(source.style)
    active_style = source.style
    for block in blocks:
        if block.end > raw_offset:
            break
        for command in _COLOR_STATE.finditer(block.content):
            active_style, color = _apply_color_state(command, source.style, active_style, catalog)
    return color


def _apply_color_state(
    command: re.Match[str],
    source_style: str,
    active_style: str,
    catalog: AssStyleCatalog,
) -> tuple[str, str]:
    if not command.group("color_command"):
        active_style = command.group("style").strip() or source_style
        return active_style, catalog.primary_color(active_style)
    explicit = command.group("color")
    if command.group("color_spec") is None:
        return active_style, catalog.primary_color(active_style)
    return active_style, explicit.upper() if explicit else "0"


def _color_override(bgr: int | str) -> str:
    value = f"{bgr:06X}" if isinstance(bgr, int) else bgr.upper()
    return rf"{{\1c&H{value}&}}"


def _apply_insertions(raw: str, insertions: Sequence[ColorInsertion]) -> str:
    ordered = sorted(insertions, key=lambda item: (item.raw_offset, not item.after_token))
    parts: list[str] = []
    cursor = 0
    for insertion in ordered:
        if not cursor <= insertion.raw_offset <= len(raw):
            raise ValueError("color insertion offsets must be monotonic and refer to source text")
        parts.append(raw[cursor : insertion.raw_offset])
        parts.append(insertion.override)
        cursor = insertion.raw_offset
    parts.append(raw[cursor:])
    return "".join(parts)


def _validated_colors(
    annotated: AnnotatedSubtitleEvent,
    colors: Mapping[int, int],
    *,
    require_unique: bool,
    reserved_colors: Iterable[int],
) -> tuple[int, ...]:
    expected = {token.token_index for token in annotated.tokens}
    if set(colors) != expected:
        raise ValueError("token colors must cover every annotation exactly")
    values = tuple(colors.values())
    if any(isinstance(value, bool) or not 0 < value <= 0xFFFFFF for value in values):
        raise ValueError("token colors must be non-zero 24-bit BGR values")
    if require_unique and len(values) != len(set(values)):
        raise ValueError("hit-map token colors must be unique")
    if set(values) & set(reserved_colors):
        raise ValueError("token color is reserved")
    return values


def _validate_rewrite_envelope(
    annotated: AnnotatedSubtitleEvent, blocks: Sequence[_OverrideBlock]
) -> None:
    decoded = annotated.decoded
    if decoded.drawings:
        raise UnsupportedAssEvent("drawing events are not color-rewritten")
    _validate_static_overrides(decoded.source, blocks)
    if any(unicodedata.bidirectional(character) in _BIDI_CLASSES for character in decoded.text):
        raise UnsupportedAssEvent("bidirectional text is outside the interactive envelope")
    for left, right in pairwise(annotated.tokens):
        if left.text_end != right.text_start:
            continue
        before = decoded.text[left.text_end - 1]
        after = decoded.text[right.text_start]
        if before.isascii() and after.isascii() and before.isalpha() and after.isalpha():
            raise UnsupportedAssEvent("a token boundary may split a Latin ligature")


def _validate_static_overrides(source: RawSubtitleEvent, blocks: Sequence[_OverrideBlock]) -> None:
    if any(_UNSAFE.search(block.content) for block in blocks):
        raise UnsupportedAssEvent("animated or karaoke overrides are not color-rewritten")
    if source.effect.strip():
        raise UnsupportedAssEvent("ASS effects are outside the static interactive envelope")
    for block in blocks:
        recognized = {
            match.start()
            for match in _COLOR_STATE.finditer(block.content)
            if match.group("color_command")
        }
        if any(
            match.start() not in recognized
            for match in _PRIMARY_COLOR_COMMAND.finditer(block.content)
        ):
            raise UnsupportedAssEvent("unparsed primary-color command is not color-rewritten")


def _token_insertions(
    annotated: AnnotatedSubtitleEvent,
    colors: Mapping[int, int],
    catalog: AssStyleCatalog,
    blocks: Sequence[_OverrideBlock],
) -> tuple[ColorInsertion, ...]:
    decoded = annotated.decoded
    raw_spans = decoded.raw_spans
    if raw_spans is None:
        raise UnsupportedAssEvent("ASS rewriting requires exact raw text spans")
    insertions: list[ColorInsertion] = []
    for token in annotated.tokens:
        token_spans = raw_spans[token.text_start : token.text_end]
        if not token_spans:
            raise ValueError("token annotation has no raw text spans")
        start, end = token_spans[0].start, token_spans[-1].end
        crossing = (block for block in blocks if start < block.start and block.end < end)
        if any(_COLOR_STATE.search(block.content) for block in crossing):
            raise UnsupportedAssEvent("a source color or reset override crosses a token")
        insertions.extend(
            (
                ColorInsertion(
                    start,
                    _color_override(colors[token.token_index]),
                    after_token=False,
                ),
                ColorInsertion(
                    end,
                    _color_override(_effective_color(decoded.source, catalog, blocks, end)),
                    after_token=True,
                ),
            )
        )
    return tuple(insertions)


def rewrite_ass_event(
    annotated: AnnotatedSubtitleEvent,
    colors: Mapping[int, int],
    catalog: AssStyleCatalog,
    *,
    require_unique: bool = False,
    reserved_colors: Iterable[int] = (),
) -> AssColorRewrite:
    """Inject token colors without altering authored bytes; reject ambiguous state."""
    _validated_colors(
        annotated,
        colors,
        require_unique=require_unique,
        reserved_colors=reserved_colors,
    )
    decoded = annotated.decoded
    raw = decoded.source.raw_text
    blocks = _blocks(raw)
    _validate_rewrite_envelope(annotated, blocks)
    insertions = _token_insertions(annotated, colors, catalog, blocks)
    rewritten = replace(decoded.source, raw_text=_apply_insertions(raw, insertions))
    return AssColorRewrite(decoded.source, rewritten, insertions)


def _validated_palette(maximum_color: int, reserved_colors: Iterable[int]) -> tuple[int, set[int]]:
    if isinstance(maximum_color, bool) or not 0 < maximum_color <= 0xFFFFFF:
        raise ValueError("maximum color must be in the 24-bit BGR range")
    reserved = set(reserved_colors)
    if any(isinstance(color, bool) or not 0 < color <= 0xFFFFFF for color in reserved):
        raise ValueError("reserved colors must be non-zero 24-bit BGR values")
    return maximum_color, reserved


def allocate_token_colors(
    events: Iterable[AnnotatedSubtitleEvent],
    *,
    reserved_colors: Iterable[int] = (),
    maximum_color: int = 0xFFFFFF,
) -> tuple[TokenColor, ...]:
    """Allocate deterministic event-aware colors for a hidden hit map."""
    maximum_color, reserved = _validated_palette(maximum_color, reserved_colors)
    available = (color for color in range(1, maximum_color + 1) if color not in reserved)
    allocated: list[TokenColor] = []
    seen: set[tuple[SubtitleEventId, int]] = set()
    for event in events:
        for token in event.tokens:
            key = (event.decoded.source.identity, token.token_index)
            if key in seen:
                raise ValueError("event/token identity is repeated")
            seen.add(key)
            try:
                color = next(available)
            except StopIteration as error:
                raise ValueError("token color palette exhausted") from error
            allocated.append(TokenColor(*key, color))
    return tuple(allocated)
