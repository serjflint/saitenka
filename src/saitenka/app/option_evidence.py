"""Bounded provenance for the options consumed by session construction."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from saitenka.app.render_evidence import EvidenceRegistry
from saitenka.app.report_schema import count

if TYPE_CHECKING:
    from saitenka.app.config import ReaderOptions

FIELDS = {
    "tooltip.tip_max_frac": "tip_height",
    "tooltip.tip_scale": "tip_scale",
    "tooltip.nested_max_frac": "nested_max_frac",
    "tooltip.pause_on_tooltip": "pause_on_tooltip",
    "tooltip.hover_switch_delay": "hover_switch_delay",
    "tooltip.scan_delay": "scan_delay",
    "tooltip.hide_delay": "hide_delay",
    "tooltip.layout_engine": "layout_engine",
    "tooltip.render_cache": "render_cache",
    "tooltip.mask_atlas": "mask_atlas",
    "tooltip.render_cache_max_mb": "render_cache_max_mb",
    "tooltip.render_cache_min_height": "render_cache_min_height",
    "panels.scale": "ui_scale",
    "prefetch": "prefetch",
    "subtitle_geometry.native_visible": "subtitle_geometry.native_visible",
    "subtitle_geometry.cache_max": "subtitle_geometry.cache_max",
    "subtitle_geometry.lookahead": "subtitle_geometry.lookahead",
}
RUN_FLAGS = frozenset(
    {
        "tip_height",
        "tip_scale",
        "pause_on_tooltip",
        "hover_switch_delay",
        "layout_engine",
        "prefetch",
    }
)
ORIGINS = frozenset(
    {
        "default",
        "derived-default",
        "config-file",
        "config-object",
        "cli",
        "programmatic",
        "programmatic-override",
        "unknown",
    }
)
registry = EvidenceRegistry()


def origins(
    cfg: dict,
    *,
    parsed: tuple[tuple[str, str], ...] | None = None,
    config_source: str = "config-object",
) -> tuple[tuple[str, str], ...]:
    cli = dict(parsed or ())
    result = {}
    for field, key in FIELDS.items():
        if parsed is not None and key in RUN_FLAGS:
            result[field] = cli.get(key, "programmatic")
            continue
        container = cfg
        parts = key.split(".")
        for part in parts[:-1]:
            container = container.get(part, {})
            container = container if isinstance(container, dict) else {}
        result[field] = config_source if parts[-1] in container else "default"
    if result["subtitle_geometry.cache_max"] == "default":
        result["subtitle_geometry.cache_max"] = "derived-default"
    return tuple(sorted(result.items()))


def _value(value: object) -> object:
    if type(value) is bool:
        return value
    if isinstance(value, (int, float)) and abs(value) <= 1e9 and math.isfinite(value):
        return value
    if isinstance(value, str) and value in {"default", "taffy"}:
        return value
    return None


def record(options: ReaderOptions) -> int:
    owner = registry.allocate()
    sources = dict(options.diagnostic_origins)
    fields = {}
    for path in FIELDS:
        value: object = options
        for key in path.split("."):
            value = getattr(value, key)
        source = sources.get(path, "unknown")
        fields[path] = {
            "value": _value(value),
            "origin": source if source in ORIGINS else "unknown",
        }
    registry.update(owner, {"owner": owner, "captured_ns": time.time_ns(), "fields": fields})
    return owner


def _safe_fields(fields: dict) -> dict:
    values = {}
    for key in FIELDS:
        field = fields.get(key)
        field = field if isinstance(field, dict) else {}
        source = field.get("origin")
        values[key] = {
            "value": _value(field.get("value")),
            "origin": source if isinstance(source, str) and source in ORIGINS else "unknown",
        }
    return values


def safe_snapshot(raw: object) -> dict:
    from saitenka.app.profile_evidence import safe_snapshot as safe_profiles

    if not isinstance(raw, dict):
        return {"status": "unknown"}
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        return {"status": "unsupported-schema"}
    owners = raw.get("owners")
    if not isinstance(owners, list) or len(owners) > 4:
        return {"status": "invalid"}
    rows = []
    seen = set()
    for owner in owners:
        if not isinstance(owner, dict) or not count(owner.get("owner")) or owner["owner"] in seen:
            return {"status": "invalid"}
        seen.add(owner["owner"])
        fields = owner.get("fields")
        if not isinstance(fields, dict):
            return {"status": "invalid"}
        rows.append(
            {
                "owner": owner["owner"],
                "captured_ns": count(owner.get("captured_ns")),
                "fields": _safe_fields(fields),
            }
        )
    return {
        "status": "collected" if rows else "unknown",
        "schema": 1,
        "scope": "session-construction options; not live overrides or renderer readback",
        "owners": rows,
        "owners_evicted": count(raw.get("owners_evicted")),
        "clock": "unix-nanoseconds",
        "profiles": safe_profiles(raw.get("profiles")),
    }
