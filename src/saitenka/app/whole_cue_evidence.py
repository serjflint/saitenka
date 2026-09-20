"""Bounded, text-free whole-cue decisions retained without tracing."""

from __future__ import annotations

import math
import re

LIMIT = 64
REASONS = frozenset(
    {
        "eligible",
        "unknown",
        "pending-whole-cue",
        "boxes-only",
        "raster-evicted",
        "composite-budget",
        "configured-overpaint",
        "osd-input",
        "render-space",
        "osd-shaper",
        "osd-justify",
        "event-order",
        "font-access",
        "kerning",
        "wrapping",
        "script-resolution",
        "layout-resolution",
        "event-layout",
        "style-layout",
        "unqualified",
        "shape-mismatch",
        "pixel-aspect",
        "renderer-state",
        "blended",
        "no-scan-regions",
        "scan-only-policy",
        "static-supported",
        "overlapping-paint",
        "karaoke",
        "alpha",
        "dynamic-geometry",
        "clipping",
        "unsupported-drawing-transform",
        "missing",
        "missing-coherent-evidence",
        "no-color-requested",
        "replaced",
        "shutdown",
        "blank",
        "source-changed",
        "episode-changed",
    }
)
_ENUMS = {
    "requested": {"legacy", "whole-cue-auto", "whole-cue-osd", "whole-cue-overpaint", "boxes-only"},
    "device": {"none", "overprint", "overpaint"},
    "event": {
        "subtitle_osd_warmup",
        "decision",
        "subtitle_color_arrival",
        "subtitle_color_target",
        "subtitle_color_target_corrected",
        "subtitle_color_submit",
        "subtitle_color_ack",
        "subtitle_color_stale_ack",
        "subtitle_color_first_ack",
        "subtitle_color_deadline",
        "subtitle_color_progress",
        "subtitle_color_outcome",
    },
    "color_status": {
        "complete",
        "partial",
        "failed",
        "unknown",
        "policy-suppressed",
        "no-color-requested",
        "no-acknowledgment",
    },
    "reason": REASONS,
    "comparison": {"unknown", "exact", "shape-mismatch", "not-run"},
    "validation_scope": {"ass-render-completion-not-display"},
}
_NUMBERS = {
    "occurrence",
    "cue_start_ms",
    "generation",
    "cue_revision",
    "captured_ns",
    "write",
    "tokens",
    "requested_tokens",
    "permitted",
    "submitted",
    "acknowledged",
    "elapsed_ms",
    "first_ms",
    "complete_ms",
    "budget_ms",
    "source_epoch",
    "layers",
    "qualification_ms",
}
_VECTORS = {
    "shadow_bounds",
    "osd_bounds",
    "osd_resolution",
    "frame_size",
    "margins",
    "delta",
    "mapping",
}


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and abs(value) < 1e20
    )


def safe_record(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}
    result: dict = {}
    for key, allowed in _ENUMS.items():
        value = raw.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    for key in _NUMBERS:
        if _number(value := raw.get(key)):
            result[key] = value
    for key in ("text_hash", "color_session"):
        if isinstance(value := raw.get(key), str) and re.fullmatch(r"[0-9a-f]{8,64}", value):
            result[key] = value
    result.update(_geometry(raw))
    return result


def _geometry(raw: dict) -> dict:
    result: dict = {}
    for key in _VECTORS:
        value = raw.get(key)
        if isinstance(value, (list, tuple)) and len(value) in {2, 4} and all(map(_number, value)):
            result[key] = list(value)
    if isinstance(value := raw.get("blockers"), (list, tuple)):
        result["blockers"] = sorted({v for v in value if isinstance(v, str) and v in REASONS})
    for key in ("accepted", "late", "scan_available"):
        if type(value := raw.get(key)) is bool:
            result[key] = value
    return result


def safe_history(raw: object) -> dict:
    if not isinstance(raw, dict) or not isinstance(rows := raw.get("history"), list):
        return {"status": "unknown"}
    if len(rows) > LIMIT:
        return {"status": "invalid"}
    counts = raw.get("counts", {})
    return {
        "status": "partial",
        "scope": "decisions, shadow predictions and mpv ASS render completions; not displayed pixels",
        "history": [safe_record(row) for row in rows],
        "evicted": raw.get("evicted") if type(raw.get("evicted")) is int else None,
        "outcomes": _outcomes(raw.get("outcomes")),
        "counts": {k: v for k, v in counts.items() if k in REASONS and type(v) is int and v >= 0}
        if isinstance(counts, dict)
        else {},
    }


def _outcomes(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        k: v for k, v in raw.items() if k in _ENUMS["color_status"] and type(v) is int and v >= 0
    }
