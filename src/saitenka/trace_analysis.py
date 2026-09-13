"""Text-free interpretation of exported operation evidence; no SDK dependency."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import isfinite
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _args(event: dict) -> dict:
    value = event.get("args")
    return value if isinstance(value, dict) else {}


def _id(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 32 else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and isfinite(value) else None


def _quality(args: dict) -> str:
    reason = args.get("soft_reason")
    if reason is None:
        return "unknown"
    return "soft" if reason else "crisp"


def tooltip_quality(events: Sequence[object]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("ph") != "X" or event.get("name") != "tip_compose":
            continue
        args = _args(event)
        kind = args.get("kind", "unknown")
        if not isinstance(kind, str) or kind not in {"base", "nested", "clicked"}:
            kind = "unknown"
        counts[kind][_quality(args)] += 1
    return {
        kind: {quality: values[quality] for quality in ("crisp", "soft", "unknown")}
        for kind, values in counts.items()
    }


def tooltip_lifecycles(events: Sequence[object]) -> dict:
    """Exported per-view evidence; acknowledgment is not a display-presentation probe."""
    views: dict[str, list[dict]] = defaultdict(list)
    names = {
        "tooltip_quality_transition",
        "tooltip_quality_submission",
        "tooltip_quality_acknowledged",
        "tooltip_quality_end",
    }
    for event in events:
        if not isinstance(event, dict) or event.get("name") not in names:
            continue
        args = _args(event)
        view = _id(args.get("view_id"))
        if view is None:
            continue
        views[view].append(
            {
                "name": event["name"],
                "ts": _number(event.get("ts")),
                **{
                    key: args[key]
                    for key in (
                        "kind",
                        "quality",
                        "previous",
                        "previous_ms",
                        "reason",
                        "viewport_revision",
                        "job_id",
                        "outcome",
                        "endpoint",
                        "acknowledged_quality",
                        "upgrade_abandoned",
                    )
                    if key in args
                },
            }
        )
    return {
        "endpoint": "submission and mpv acknowledgment; display unmeasured",
        "views": {
            view: sorted(rows, key=lambda row: row["ts"] or 0) for view, rows in views.items()
        },
    }


def _cycles(parents: dict[str, str | None]) -> list[str]:
    cycles: set[str] = set()
    visited: set[str] = set()
    for key in parents:
        path: dict[str, int] = {}
        cursor: str | None = key
        while cursor in parents and cursor not in visited and cursor not in path:
            assert cursor is not None
            path[cursor] = len(path)
            cursor = parents[cursor]
        if cursor in path:
            cycles.update(list(path)[path[cursor] :])
        visited.update(path)
    return sorted(cycles)


def _late(child: dict, parent: dict) -> bool:
    start, parent_start, duration = (
        _number(child.get("ts")),
        _number(parent.get("ts")),
        _number(parent.get("dur")),
    )
    if start is None or parent_start is None or duration is None:
        return False
    return start > parent_start + duration


def _duplicate_ids(ids: list[str | None]) -> int:
    return sum(count - 1 for key, count in Counter(ids).items() if key and count > 1)


def parent_tree_health(events: Sequence[object]) -> dict:
    spans = [event for event in events if isinstance(event, dict) and event.get("ph") == "X"]
    ids = [_id(_args(event).get("span_id")) for event in spans]
    index = {key: event for key, event in zip(ids, spans, strict=True) if key}
    parents = {key: _id(_args(event).get("parent_id")) for key, event in index.items()}
    missing = [key for key, parent in parents.items() if parent and parent not in index]
    late = [
        key
        for key, parent in parents.items()
        if parent in index and _late(index[key], index[parent])
    ]
    startup_leaks = [
        key
        for key in late
        if index[parents[key] or ""].get("name")
        in {"startup.reader_create", "startup.subtitle_index"}
    ]
    return {
        "spans": len(spans),
        "missing_ids": ids.count(None),
        "duplicate_ids": _duplicate_ids(ids),
        "unresolved_parents": missing,
        "cycles": _cycles(parents),
        "late_children": late,
        "suspected_startup_context_leaks": startup_leaks,
        "late_children_are_invalid": False,
    }
