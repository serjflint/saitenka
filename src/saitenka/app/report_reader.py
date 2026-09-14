"""Bounded reads of untrusted local diagnostic artifacts; never extract an archive."""

from __future__ import annotations

import json
import math
import stat
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 256


def trace_evidence(source: Path, *, limit: int = MAX_MEMBER_BYTES) -> dict:
    """Keep absent, invalid and empty captures distinct; never discard malformed rows silently."""
    raw: str | None
    try:
        if source.is_file() and source.suffix == ".json":
            raw = read_file(source, limit=limit)
        else:
            raw = read_member(source, "telemetry/trace.json", limit=limit)
            if raw is None:
                raw = read_member(source, "trace.json", limit=limit)
        if raw is None:
            return {"status": "missing", "events": [], "reason": "trace-not-collected"}
        return _trace_document(json.loads(raw))
    except json.JSONDecodeError:
        return {"status": "invalid", "events": [], "reason": "invalid-trace-json"}
    except ValueError as error:
        return {"status": "invalid", "events": [], "reason": str(error)}
    except (OSError, RecursionError):
        return {"status": "invalid", "events": [], "reason": "unreadable-trace"}


def _trace_document(document: object) -> dict:
    metadata = document.get("otherData") if isinstance(document, dict) else None
    if isinstance(metadata, dict) and "schema_version" in metadata:
        version = metadata["schema_version"]
        if type(version) is not int or version not in {1, 2}:
            return {"status": "invalid", "events": [], "reason": "unsupported-trace-schema"}
    rows = document.get("traceEvents") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        return {"status": "invalid", "events": [], "reason": "invalid-trace-schema"}
    valid = [row for row in rows if _trace_event(row)]
    invalid = len(rows) - len(valid)
    events = valid
    if isinstance(metadata, dict) and isinstance(metadata.get("session"), str):
        events = [{"ph": "M", "name": "session", "args": metadata}, *valid]
    if invalid:
        return {
            "status": "partial" if valid else "invalid",
            "events": events,
            "reason": "invalid-trace-events",
            "declared_events": len(rows),
            "invalid_events": invalid,
        }
    return {"status": "readable", "events": events, "declared_events": len(rows)}


def _trace_event(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    if not isinstance(row.get("name"), str) or not isinstance(row.get("ph"), str):
        return False
    if not isinstance(row.get("args", {}), dict):
        return False
    if row["ph"] == "X" and not {"ts", "dur"} <= row.keys():
        return False
    for key in ("ts", "dur"):
        value = row.get(key, 0)
        if (
            type(value) not in {int, float}
            or abs(value) > 1e18
            or not math.isfinite(value)
            or value < 0
        ):
            return False
    return all(type(row.get(key, 0)) in {str, int} for key in ("pid", "tid"))


def read_file(path: Path, *, limit: int = MAX_MEMBER_BYTES) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("diagnostic member exceeds byte limit")
    return raw.decode("utf-8", errors="replace")


def _safe_name(name: str) -> bool:
    parts = name.rstrip("/").split("/")
    return (
        bool(name)
        and not name.startswith("/")
        and not any(part in {"", "..", "."} or ":" in part or "\\" in part for part in parts)
    )


def _validate_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ValueError("diagnostic archive exceeds member limit")
    if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
        raise ValueError("diagnostic archive exceeds expanded byte limit")
    names = [item.filename for item in members]
    if len(names) != len(set(names)):
        raise ValueError("ambiguous duplicate diagnostic member")
    if any(
        not _safe_name(item.filename) or stat.S_ISLNK(item.external_attr >> 16) for item in members
    ):
        raise ValueError("unsafe diagnostic member path")
    return members


def read_member(source: Path, name: str, *, limit: int = MAX_MEMBER_BYTES) -> str | None:
    if not _safe_name(name):
        raise ValueError("unsafe diagnostic member path")
    if source.is_dir():
        path = source / name
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("diagnostic member escapes source directory")
        return read_file(path, limit=limit) if path.is_file() else None
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("diagnostic archive exceeds byte limit")
    try:
        with zipfile.ZipFile(source) as archive:
            members = _validate_members(archive)
            matches = [
                item
                for item in members
                if item.filename == name or item.filename.endswith("/" + name)
            ]
            if not matches:
                return None
            if len(matches) != 1:
                raise ValueError("ambiguous diagnostic member suffix")
            member = matches[0]
            if member.file_size > limit:
                raise ValueError("diagnostic member exceeds byte limit")
            with archive.open(member) as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("diagnostic member exceeds byte limit")
            return raw.decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError("not a valid report archive") from error
