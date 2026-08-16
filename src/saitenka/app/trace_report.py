"""Installed, text-free startup and annotation trace summary."""

from __future__ import annotations

import json
import math
import zipfile
from typing import TYPE_CHECKING

from saitenka.app.subtitle_report import load_trace

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SPAN_NAMES = frozenset(
    {
        "startup.mpv_connect",
        "startup.subtitle_selection",
        "startup.reader_create",
        "startup.subtitle_index",
        "startup.subtitle_mode_configure",
        "startup.reader_setup",
        "startup.first_tick",
        "startup.interactive_ready",
        "startup.hint",
        "cue_annotation",
        "dictionary_attestation",
    }
)
_FIELDS = frozenset(
    {
        "operation",
        "outcome",
        "connection_epoch",
        "reply_latency_ms",
        "priority",
        "phase",
        "chars",
        "queue_wait_ms",
        "work_ms",
        "token_count",
        "requested_forms",
        "selected_dictionaries",
        "hit_count",
        "cue_pending",
        "deps_pending",
        "hint_owned",
        "since_ipc_ms",
        "cpu_ms",
        "failure",
    }
)
_MAX_RECORDS = 256
_MAX_TRACE_BYTES = 64 * 1024 * 1024
_MAX_STRING_CHARS = 128
_MAX_ABS_NUMBER = 1e18
_SCALAR = (str, int, float, bool)
_LIMIT_ERROR = "trace JSON exceeds the 64 MiB diagnostic limit"


def _safe_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return value if abs(value) <= _MAX_ABS_NUMBER else None
    return value if math.isfinite(value) and abs(value) <= _MAX_ABS_NUMBER else None


def _safe_args(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in _FIELDS:
        field = value.get(key)
        if not isinstance(field, _SCALAR):
            continue
        if isinstance(field, str) and len(field) > _MAX_STRING_CHARS:
            continue
        if not isinstance(field, (str, bool)) and _safe_number(field) is None:
            continue
        result[key] = field
    return result


def _startup_records(events: Sequence[object]) -> tuple[list[dict], int]:
    records: list[dict] = []
    total = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        name = event.get("name", "")
        duration = _safe_number(event.get("dur", 0))
        timestamp = _safe_number(event.get("ts", 0))
        if (
            not isinstance(name, str)
            or len(name) > _MAX_STRING_CHARS
            or duration is None
            or timestamp is None
        ):
            continue
        if event.get("ph") != "X" or not (
            name in _SPAN_NAMES or name.startswith(("startup.first_tick.", "startup.reader_setup."))
        ):
            continue
        total += 1
        if len(records) >= _MAX_RECORDS:
            continue
        records.append(
            {
                "name": name,
                "ts": timestamp,
                "duration_ms": round(float(duration) / 1_000, 3),
                "args": _safe_args(event.get("args", {})),
            }
        )
    return sorted(records, key=lambda record: record["ts"]), total


def startup_records(events: Sequence[object]) -> list[dict]:
    records, _total = _startup_records(events)
    return records


def render_startup(source: Path, events: list[dict]) -> str:
    records = startup_records(events)
    lines = [f"# saitenka startup trace — {source.name}"]
    ready = next(
        (record for record in records if record["name"] == "startup.interactive_ready"), None
    )
    if ready is None:
        lines.append("interactive readiness: not recorded")
    else:
        elapsed = ready["args"].get("since_ipc_ms", "unknown")
        lines.append(f"interactive readiness: {elapsed} ms after IPC connect")
    lines.append("slow startup/annotation spans:")
    ranked = sorted(records, key=lambda record: record["duration_ms"], reverse=True)
    for record in ranked[:12]:
        attrs = " ".join(f"{key}={value}" for key, value in sorted(record["args"].items()))
        lines.append(f"  {record['duration_ms']:9.3f} ms  {record['name']} {attrs}".rstrip())
    return "\n".join(lines) + "\n"


def load_startup_report(source: Path) -> tuple[list[dict], list[dict]]:
    events = load_startup_trace(source)
    return events, startup_records(events)


def _check_trace_file(path: Path) -> None:
    if path.exists() and path.stat().st_size > _MAX_TRACE_BYTES:
        raise ValueError(_LIMIT_ERROR)


def _check_trace_archive(source: Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            members = [
                item
                for item in archive.infolist()
                if item.filename == "trace.json"
                or item.filename.endswith(("/trace.json", "telemetry/trace.json"))
            ]
            if any(item.file_size > _MAX_TRACE_BYTES for item in members):
                raise ValueError(_LIMIT_ERROR)
    except zipfile.BadZipFile as error:
        raise ValueError(f"not a valid report archive: {source}") from error


def load_startup_trace(source: Path) -> list[dict]:
    """Load a trace only after bounding its uncompressed input size."""
    if source.is_file() and source.suffix == ".json":
        _check_trace_file(source)
    elif source.is_dir():
        for relative in ("telemetry/trace.json", "trace.json"):
            _check_trace_file(source / relative)
    else:
        _check_trace_archive(source)
    return load_trace(source)


def startup_json(events: Sequence[object]) -> str:
    records, total = _startup_records(events)
    return json.dumps(
        {"startup": records, "total": total, "dropped": total - len(records)},
        ensure_ascii=False,
        indent=2,
    )
