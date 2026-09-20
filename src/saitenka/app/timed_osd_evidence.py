"""Bounded metadata for timed publication; acceptance never implies presentation."""

from __future__ import annotations

import math
import re

LIMIT = 128
EVENTS = frozenset(
    {
        "prepared",
        "submit",
        "ack",
        "selected",
        "remove",
        "removed",
        "discarded",
        "remove-failed",
        "remove-rejected",
        "stage-failed",
        "stage-rejected",
        "capability",
        "capability-unavailable",
        "invalidate",
        "declined",
        "evict",
        "context",
        "connection-replaced",
    }
)
NUMBERS = frozenset(
    {
        "captured_ns",
        "epoch",
        "entry_epoch",
        "connection_epoch",
        "slot",
        "start_ms",
        "end_ms",
        "video_start_ms",
        "video_end_ms",
        "delay_ms",
        "occurrence",
        "generation",
        "width",
        "height",
    }
)


def safe_record(raw: dict) -> dict:
    result: dict = {
        key: value
        for key in NUMBERS
        if isinstance(value := raw.get(key), int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and abs(value) < 1e20
    }
    for key in ("text_hash", "payload_hash"):
        if isinstance(value := raw.get(key), str) and re.fullmatch(r"[a-f0-9]{16,64}", value):
            result[key] = value
    for key, allowed in {
        "event": EVENTS,
        "state": {"prepared", "pending", "ready", "retiring", "uncertain"},
        "supported": {True, False, "unknown"},
        "reason": {
            "context-changed",
            "clock-or-event-ineligible",
            "capacity",
            "unmatched-occurrence",
            "closed",
            "stage-failed",
            "stage-rejected",
            "invalidated",
            "prepared-inputs",
            "source-replaced",
            "annotation-dependencies",
            "annotation-or-geometry",
            "geometry-input",
            "renderer-changed",
            "clear-pixels",
            "legacy-ownership",
            "deactivate",
            "suspend",
            "playlist",
            "sub-delay",
            "osd-dimensions",
            "sid",
        },
    }.items():
        value = raw.get(key)
        if isinstance(value, str | bool) and value in allowed:
            result[key] = value
    return result


def safe_history(raw: object) -> dict:
    if not isinstance(raw, dict) or not isinstance(rows := raw.get("history"), list):
        return {"status": "unavailable", "display_counters": None}
    if len(rows) > LIMIT or not all(isinstance(row, dict) for row in rows):
        return {"status": "invalid", "display_counters": None}
    counts = raw.get("counts", {})
    return {
        "schema": 1,
        "status": "partial",
        "endpoint": "mpv-accepted-not-presentation",
        "history": [safe_record(row) for row in rows],
        "evicted": raw.get("evicted")
        if type(raw.get("evicted")) is int and raw["evicted"] >= 0
        else None,
        "counts": {
            key: value
            for key, value in counts.items()
            if key in EVENTS and type(value) is int and value >= 0
        }
        if isinstance(counts, dict)
        else {},
        "display_counters": None,
    }
