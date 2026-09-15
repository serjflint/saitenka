"""Bounded decoding of mpv's output-render subtitle-layout contract."""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from saitenka_subtitles.geometry import Rect

CAPABILITIES = frozenset({"events", "unit-logical-rects", "event-geometry-profile"})
MAX_UNITS = 4096
MAX_TEXT_BYTES = 32768


@dataclass(frozen=True, slots=True)
class LayoutUnit:
    start: int
    end: int
    regions: tuple[Rect, ...]


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
    start: float
    end: float
    units: tuple[LayoutUnit, ...]


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
    """Accept one static logical event; ambiguous simultaneous events have no action binding."""
    data = _object(value)
    if not supports_layout(data):
        raise ValueError("layout-capabilities")
    if data.get("available") is not True or data.get("status") != "ok":
        raise ValueError("layout-unavailable")
    events = data.get("events")
    if not isinstance(events, list) or len(events) != 1:
        raise ValueError("layout-event-count")
    event = _object(events[0])
    if event.get("geometry_profile") != "static-logical-v1":
        raise ValueError("layout-geometry-profile")
    if event.get("text_index_unit") != "utf8-byte":
        raise ValueError("layout-text-index")
    text = event.get("text")
    if not isinstance(text, str) or not text or len(text) > MAX_TEXT_BYTES:
        raise ValueError("layout-text")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError("layout-text")
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
    if any(
        _number(space.get(key)) != 0
        for key in ("margin_top", "margin_bottom", "margin_left", "margin_right")
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
