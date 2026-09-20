"""Numeric, bounded color metrics safe for metadata-only diagnostic bundles."""

from __future__ import annotations

_METRICS = frozenset(
    {
        "color_outcomes",
        "color_deadline_misses",
        "color_pending",
        "color_ack_ms",
        "color_withdrawals",
        "color_latency_ms",
        "color_late",
    }
)
_LABELS = {
    "kind": {"natural", "navigation"},
    "endpoint": {"first", "complete"},
    "status": {
        "complete",
        "partial",
        "failed",
        "unknown",
        "policy-suppressed",
        "no-color-requested",
        "no-acknowledgment",
    },
    "reason": {"replaced", "shutdown", "blank", "source-changed", "episode-changed"},
}


def _numbers(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key in ("value", "count", "max", "p50", "p95", "p99", "sum")
        if isinstance(value := raw.get(key), int | float)
        and not isinstance(value, bool)
        and 0 <= value <= 1e15
    }


def _label_allowed(label: object) -> bool:
    if not isinstance(label, str) or len(label) > 160:
        return False
    pairs = [part.partition("=") for part in label.split(",")]
    return (
        bool(pairs)
        and len(pairs) <= 3
        and all(
            key in _LABELS and separator == "=" and value in _LABELS[key]
            for key, separator, value in pairs
        )
    )


def _bounded_labels(record: object) -> dict:
    labels = record.get("by") if isinstance(record, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {
        label: _numbers(value if isinstance(value, dict) else {"value": value})
        for label, value in list(labels.items())[:128]
        if _label_allowed(label)
    }


def safe_color_metrics(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unavailable"}
    metrics = {}
    for suffix in sorted(_METRICS):
        name = f"saitenka.subtitle.{suffix}"
        record = raw.get(name)
        numeric = _numbers(record)
        if not numeric:
            continue
        if labels := _bounded_labels(record):
            numeric["by"] = labels
        metrics[name] = numeric
    return {"status": "collected" if metrics else "unavailable", "metrics": metrics}
