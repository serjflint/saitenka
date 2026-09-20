"""Bounded decoding of mpv's output-render subtitle-layout contract."""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any

from saitenka_subtitles.geometry import Rect

CAPABILITIES = frozenset({"events", "unit-logical-rects", "event-geometry-profile"})
MAX_EVENTS = 64
MAX_UNITS = 4096
MAX_TEXT_BYTES = 32768


@dataclass(frozen=True, slots=True)
class LayoutUnit:
    start: int
    end: int
    regions: tuple[Rect, ...]


@dataclass(frozen=True, slots=True)
class LayoutEvent:
    track_index: int
    text: str
    start: float
    end: float
    units: tuple[LayoutUnit, ...]


@dataclass(frozen=True, slots=True)
class LayoutSnapshot:
    snapshot_id: str
    revision: int
    playlist_entry_id: int
    track_id: int
    decoder_generation: int
    format: str
    size: tuple[int, int]
    text: str
    start: float | None
    end: float | None
    units: tuple[LayoutUnit, ...]
    events: tuple[LayoutEvent, ...]


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("layout-object")
    return value


def _integer(value: Any, low: int = 0, high: int = 2**63 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("layout-integer")
    return value


def _number(value: Any) -> float:
    if type(value) not in {int, float} or abs(value) > 10**9 or not math.isfinite(value):
        raise ValueError("layout-number")
    return float(value)


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_BYTES:
        raise ValueError("layout-text")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("layout-text")
    return value


def supports_layout(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    caps = value.get("capabilities")
    return (
        isinstance(caps, list)
        and len(caps) <= 64
        and all(isinstance(cap, str) for cap in caps)
        and set(caps) >= CAPABILITIES
    )


def _regions(value: Any, size: tuple[int, int]) -> tuple[Rect, ...]:
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("layout-regions")
    result = []
    for item in value:
        rect = _object(item)
        x, y, w, h = (_number(rect.get(key)) for key in ("x", "y", "w", "h"))
        if w < 0 or h < 0:
            raise ValueError("layout-extent")
        left, top = max(0, math.floor(x)), max(0, math.floor(y))
        right, bottom = min(size[0], math.ceil(x + w)), min(size[1], math.ceil(y + h))
        if right > left and bottom > top:
            result.append(Rect(left, top, right - left, bottom - top))
    return tuple(result)


def decode_layout(value: object) -> LayoutSnapshot:
    """Decode bounded final-output regions, preserving every event's timing and identity."""
    data = _object(value)
    if not supports_layout(data):
        raise ValueError("layout-capabilities")
    if data.get("available") is not True or data.get("status") != "ok":
        if data.get("status") == "unsupported-render-mode":
            raise ValueError("layout-unsupported-render-mode")
        raise ValueError("layout-unavailable")
    events = data.get("events")
    if not isinstance(events, list) or not 0 < len(events) <= MAX_EVENTS:
        raise ValueError("layout-event-count")
    if len(events) > 1:
        return _decode_multiple(data, events)
    event = _object(events[0])
    if event.get("geometry_profile") not in {"static-logical-v1", "static-logical-v2"}:
        raise ValueError("layout-geometry-profile")
    if event.get("text_index_unit") != "utf8-byte":
        raise ValueError("layout-text-index")
    text = _text(event.get("text"))
    encoded = text.encode("utf-8")
    space = _object(data.get("space"))
    if (space.get("name"), space.get("render_origin"), space.get("rect_semantics")) != (
        "osd",
        "output-render",
        "half-open",
    ):
        raise ValueError("layout-space")
    size = (_integer(space.get("width"), 1, 16384), _integer(space.get("height"), 1, 16384))
    if _number(space.get("display_par")) <= 0:
        raise ValueError("layout-par")
    margins = tuple(
        _number(space.get(key))
        for key in ("margin_top", "margin_bottom", "margin_left", "margin_right")
    )
    if (
        any(value < 0 for value in margins)
        or margins[0] + margins[1] >= size[1]
        or margins[2] + margins[3] >= size[0]
        or (any(margins) and event.get("geometry_profile") != "static-logical-v2")
    ):
        raise ValueError("layout-margins")
    track = _object(data.get("track"))
    if track.get("format") not in {"ass", "subrip"} or track.get("layout_engine") != "libass":
        raise ValueError("layout-track")
    if track.get("layout_unit_mode") not in {"harfbuzz-cluster", "simple-scalar"}:
        raise ValueError("layout-unit-mode")
    if _object(data.get("time")).get("event_domain") != "subtitle":
        raise ValueError("layout-clock")
    start, end = _number(event.get("start")), _number(event.get("end"))
    if end <= start:
        raise ValueError("layout-time-range")
    units = event.get("units")
    if not isinstance(units, list) or not 0 < len(units) <= MAX_UNITS:
        raise ValueError("layout-unit-count")
    boundaries = {0}
    cursor = 0
    for char in text:
        cursor += len(char.encode("utf-8"))
        boundaries.add(cursor)
    decoded = []
    for item in units:
        unit = _object(item)
        a = _integer(unit.get("start"), 0, len(encoded))
        b = _integer(unit.get("end"), a + 1, len(encoded))
        if a not in boundaries or b not in boundaries:
            raise ValueError("layout-utf8-boundary")
        decoded.append(LayoutUnit(a, b, _regions(unit.get("logical_rects"), size)))
    decoded.sort(key=lambda unit: unit.start)
    if any(a.end > b.start for a, b in pairwise(decoded)):
        raise ValueError("layout-overlapping-units")
    snapshot_id = data.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not 0 < len(snapshot_id) <= 128:
        raise ValueError("layout-snapshot-id")
    return LayoutSnapshot(
        snapshot_id,
        _integer(data.get("revision")),
        _integer(_object(data.get("media")).get("playlist_entry_id")),
        _integer(track.get("id")),
        _integer(track.get("decoder_generation")),
        track["format"],
        size,
        text,
        start,
        end,
        tuple(decoded),
        (LayoutEvent(_integer(event.get("track_index", 0)), text, start, end, tuple(decoded)),),
    )


def _decode_multiple(data: dict[str, Any], raw_events: list[Any]) -> LayoutSnapshot:
    if "event-track-index" not in data["capabilities"]:
        raise ValueError("layout-event-count")
    events = [_object(event) for event in raw_events]
    indices = [_integer(event.get("track_index")) for event in events]
    if len(set(indices)) != len(indices):
        raise ValueError("layout-event-identity")
    texts = [_text(event.get("text")) for event in events]
    if sum(len(text.encode("utf-8")) for text in texts) + len(texts) - 1 > MAX_TEXT_BYTES:
        raise ValueError("layout-text")
    if any(not isinstance(event.get("units"), list) for event in events):
        raise ValueError("layout-unit-count")
    if sum(len(event["units"]) for event in events) > MAX_UNITS:
        raise ValueError("layout-unit-count")
    parts = [
        decode_layout({**data, "events": [event]})
        for _, event in sorted(zip(indices, events, strict=True))
    ]
    units: list[LayoutUnit] = []
    offset = 0
    for part in parts:
        units.extend(
            replace(unit, start=unit.start + offset, end=unit.end + offset) for unit in part.units
        )
        offset += len(part.text.encode("utf-8")) + 1
    return replace(
        parts[0],
        text="\n".join(part.text for part in parts),
        start=None,
        end=None,
        units=tuple(units),
        events=tuple(part.events[0] for part in parts),
    )


def token_regions(
    layout: LayoutSnapshot, spans: tuple[tuple[int, int], ...]
) -> tuple[tuple[Rect, ...], ...]:
    """Map codepoint spans without splitting clusters or making gaps between regions hittable."""
    if len(spans) > MAX_UNITS:
        raise ValueError("layout-token-count")
    offsets = [0]
    for char in layout.text:
        offsets.append(offsets[-1] + len(char.encode("utf-8")))
    starts = [unit.start for unit in layout.units]
    result = []
    previous = 0
    for start, end in spans:
        if not 0 <= previous <= start < end < len(offsets):
            raise ValueError("layout-token-span")
        previous = end
        a, b = offsets[start], offsets[end]
        lo, hi = bisect_left(starts, a), bisect_left(starts, b)
        units = layout.units[lo:hi]
        complete = bool(units) and units[0].start == a and units[-1].end == b
        adjacent = all(x.end == y.start for x, y in pairwise(units))
        result.append(
            tuple(rect for unit in units for rect in unit.regions) if complete and adjacent else ()
        )
    return tuple(result)
