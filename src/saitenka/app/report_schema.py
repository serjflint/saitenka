"""Allowlisted metadata for shareable reports; free-form evidence is opt-in."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from functools import cache
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

SCHEMA_VERSION = 1
_OPERATIONS = frozenset(
    {
        "anki_request",
        "mpv_effect",
        "runtime_job",
        "runtime_mpv",
        "surface_write",
        "tooltip_lifetime",
        "scroll_lifetime",
        "tooltip_quality_submission",
        "subtitle_device_upload",
    }
)
_OUTCOMES = frozenset(
    {
        "succeeded",
        "acknowledged",
        "failed",
        "cancelled",
        "superseded",
        "stale",
        "not-admitted",
        "unavailable",
        "timeout",
        "disconnected",
        "shutdown-aborted",
    }
)
_VERSION = re.compile(
    r"[0-9]+(?:\.[0-9]+){1,3}(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+g[0-9a-f]{7,40}(?:-dirty)?)?"
)


@cache
def startup_source_identity() -> dict:
    import saitenka_subtitles

    import saitenka

    digest = hashlib.sha256()
    try:
        for package in (saitenka, saitenka_subtitles):
            if package.__file__ is None:
                return {"scope": "unavailable"}
            root = Path(package.__file__).parent
            for path in sorted(root.rglob("*.py")):
                digest.update(str(path.relative_to(root)).encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
        return {
            "sha256": digest.hexdigest(),
            "captured_ns": time.time_ns(),
            "scope": "startup-python-source",
        }
    except OSError:
        return {"scope": "unavailable"}


def build_identity() -> dict:
    from saitenka.version import overlay_version

    editable = None
    try:
        # Distribution.read_text owns UTF-8 decoding and accepts no encoding argument.
        # ast-grep-ignore: read-text-utf8-encoding
        direct = distribution("saitenka").read_text("direct_url.json")
        if direct is not None:
            editable = json.loads(direct).get("dir_info", {}).get("editable", False)
            if type(editable) is not bool:
                editable = None
    except (PackageNotFoundError, OSError, ValueError, AttributeError, RecursionError):
        pass
    try:
        build = overlay_version()
    except (OSError, subprocess.SubprocessError):
        build = "unknown"
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    return {
        "overlay_build": build,
        "source": dict(startup_source_identity()),
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "gil": "on" if gil else "off",
        "editable": editable,
        "loaded_native_runtime": "unknown",
    }


def safe_identity(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    result: dict = {}
    for key in ("overlay_build", "python_version"):
        value = source.get(key)
        result[key] = (
            value
            if isinstance(value, str) and len(value) <= 96 and _VERSION.fullmatch(value)
            else "unknown"
        )
    for key, allowed in (
        ("platform", {"darwin", "linux", "win32", "freebsd"}),
        ("gil", {"on", "off"}),
    ):
        value = source.get(key)
        result[key] = value if isinstance(value, str) and value in allowed else "unknown"
    value = source.get("editable")
    result["editable"] = value if type(value) is bool else None
    identity = source.get("source")
    if (
        isinstance(identity, dict)
        and isinstance(digest := identity.get("sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", digest)
    ):
        result["source"] = {
            "sha256": digest,
            "captured_ns": count(identity.get("captured_ns")),
            "scope": "startup-python-source",
        }
    result["loaded_native_runtime"] = "unknown"
    return result


def count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def operation_health(raw: dict) -> dict:
    pending, outcomes = raw.get("pending"), raw.get("outcomes")
    result: dict = {"scope": "deferred-operation boundaries; not all product operations"}
    if not isinstance(pending, dict) or not isinstance(outcomes, list):
        return {**result, "status": "invalid"}
    pending_counts = [count(value) for value in pending.values()]
    outcome_counts = [
        count(row.get("count")) if isinstance(row, dict) else None for row in outcomes
    ]
    if None in pending_counts or None in outcome_counts:
        return {**result, "status": "invalid"}
    terminal = dict.fromkeys(
        (
            "succeeded",
            "acknowledged",
            "failed",
            "cancelled",
            "superseded",
            "stale",
            "not-admitted",
            "other",
        ),
        0,
    )
    for row in outcomes:
        label = row.get("outcome")
        name = label if isinstance(label, str) and label in terminal else "other"
        terminal[name] += row["count"]
    return {
        **result,
        "status": "collected",
        "pending_total": sum(value for value in pending_counts if value is not None),
        "terminal_totals": terminal,
        "by_operation": _operation_counts(pending, outcomes),
    }


def _operation_counts(pending: dict, outcomes: list) -> dict:
    grouped: dict[str, Counter] = {}
    for row in outcomes:
        operation = row.get("operation")
        operation = (
            operation if isinstance(operation, str) and operation in _OPERATIONS else "other"
        )
        outcome = row.get("outcome")
        outcome = outcome if isinstance(outcome, str) and outcome in _OUTCOMES else "other"
        grouped.setdefault(operation, Counter())[outcome] += row["count"]
    for operation, pending_count in pending.items():
        name = operation if operation in _OPERATIONS else "other"
        grouped.setdefault(name, Counter())["pending"] += pending_count
    return {key: dict(values) for key, values in sorted(grouped.items())}


def configured_metadata(raw: dict) -> dict:
    """File values only: never imply that a running player consumed the collector's config."""
    fields = {}
    for key in ("tip_scale", "tip_height"):
        value = raw.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(value) <= 1e9
            and math.isfinite(value)
        ):
            fields[key] = {"value": value, "origin": "collector-config-file"}
    for key in ("prefetch", "resync"):
        value = raw.get(key)
        if type(value) is bool:
            fields[key] = {"value": value, "origin": "collector-config-file"}
    fields.update(_configured_geometry(raw.get("subtitle_geometry")))
    return {"scope": "configured subset, not effective runtime settings", "fields": fields}


def _configured_geometry(geometry: object) -> dict:
    fields: dict[str, dict] = {}
    if not isinstance(geometry, dict):
        return fields
    for key in ("native_visible", "cache_max", "lookahead"):
        value = geometry.get(key)
        expected = bool if key == "native_visible" else int
        if type(value) is expected and abs(value) <= 1e9:
            fields[f"subtitle_geometry.{key}"] = {
                "value": value,
                "origin": "collector-config-file",
            }
    for key, choices in (
        ("native_formats", {"authored-ass", "all"}),
        (
            "coloring",
            {"legacy", "whole-cue-auto", "whole-cue-osd", "whole-cue-overpaint", "boxes-only"},
        ),
    ):
        value = geometry.get(key)
        if isinstance(value, str) and value in choices:
            fields[f"subtitle_geometry.{key}"] = {"value": value, "origin": "collector-config-file"}
    return fields


def envelope(
    *,
    collector: dict,
    producer: dict,
    configuration: dict,
    health: dict,
    runtime: dict | None = None,
    player: dict | None = None,
    queries: dict | None = None,
    options: dict | None = None,
) -> dict:
    collector = safe_identity(collector)
    identity = safe_identity(producer.get("identity", {}))
    differing = [
        key
        for key, value in identity.items()
        if value is not None
        and value != "unknown"
        and collector.get(key) is not None
        and collector.get(key) != "unknown"
        and (
            value.get("sha256") != collector[key].get("sha256")
            if key == "source"
            else value != collector[key]
        )
    ]
    return {
        "schema": SCHEMA_VERSION,
        "privacy": "metadata-only",
        "collector": collector,
        "producer": producer,
        "identity_comparison": {
            "status": "different" if differing else "unknown",
            "differing_fields": differing,
            "scope": "recorded metadata; not proof of identical loaded code",
        },
        "configuration": configuration,
        "operation_health": health,
        "effective_runtime_configuration": runtime
        if runtime is not None
        else {"status": "unknown"},
        "pixel_fidelity": {"status": "unknown", "scope": "not-validated"},
        "player_configuration": player if player is not None else {"status": "unknown"},
        "player_query_health": queries if queries is not None else {"status": "unknown"},
        "session_configuration": options if options is not None else {"status": "unknown"},
        "omitted": ["raw-config", "logs", "traces", "crashes", "paths", "text", "pixels"],
    }
