"""Privacy-bounded mpv option values consumed by native geometry decisions."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.render_evidence import EvidenceRegistry
from saitenka.app.report_schema import count

if TYPE_CHECKING:
    from collections.abc import Mapping

_BOOL = frozenset(
    {
        "sub-ass-scale-with-window",
        "sub-use-margins",
        "sub-ass-force-margins",
        "sub-scale-with-window",
        "sub-scale-by-window",
        "sub-filter-sdh",
        "sub-ass-justify",
        "sub-scale-signs",
        "embeddedfonts",
        "sub-bold",
        "sub-italic",
        "sub-justify",
        "blend-subtitles",
        "sub-ass-override",
    }
)
_NUMBER = frozenset(
    {
        "sub-scale",
        "sub-pos",
        "sub-ass-video-aspect-override",
        "video-rotate",
        "sub-line-spacing",
        "sub-font-size",
        "sub-outline-size",
        "sub-shadow-offset",
        "sub-spacing",
        "sub-margin-x",
        "sub-margin-y",
        "sub-blur",
    }
)
_ENUM = {
    "sub-ass-use-video-data": {"none", "all", "aspect-ratio"},
    "sub-ass-override": {"no", "yes", "force", "scale", "strip"},
    "blend-subtitles": {"no", "yes", "video"},
    "sub-shaper": {"simple", "complex"},
    "sub-hinting": {"none", "light", "normal", "native"},
    "sub-font-provider": {"auto", "none", "fontconfig"},
    "osd-font-provider": {"auto", "none", "fontconfig"},
    "sub-align-x": {"left", "center", "right"},
    "sub-align-y": {"top", "center", "bottom"},
    "sub-border-style": {"outline-and-shadow", "opaque-box", "background-box"},
}
_PRIVATE = frozenset(
    {
        "sub-font",
        "sub-fonts-dir",
        "osd-fonts-dir",
        "sub-ass-style-overrides",
        "video-crop",
        "sub-color",
        "sub-outline-color",
        "sub-back-color",
    }
)
OPTIONS = _BOOL | _NUMBER | _ENUM.keys() | _PRIVATE


def _value(name: str, value: object) -> dict:
    if value is None:
        return {"status": "unavailable", "reason": "reader-returned-none"}
    if name in _PRIVATE:
        return {"status": "redacted"}
    valid = (
        (name in _BOOL and type(value) is bool)
        or (
            name in _NUMBER
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(value) <= 1e9
            and math.isfinite(value)
        )
        or (name in _ENUM and isinstance(value, str) and value in _ENUM[name])
    )
    return {"status": "available", "value": value} if valid else {"status": "invalid"}


def fields(settings: Mapping[str, object]) -> dict:
    return {name: _value(name, settings.get(name)) for name in sorted(OPTIONS)}


def _safe_field(name: str, raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    status = raw.get("status")
    if status == "available":
        return _value(name, raw.get("value"))
    if status == "unavailable":
        return {"status": "unavailable", "reason": "reader-returned-none"}
    if status == "redacted" and name in _PRIVATE:
        return {"status": "redacted"}
    return {"status": "invalid" if status == "invalid" else "unknown"}


def _configuration(row: dict) -> dict:
    values = row.get("fields")
    values = values if isinstance(values, dict) else {}
    source = row.get("source_class")
    return {
        "revision": row["revision"],
        "captured_ns": count(row.get("captured_ns")),
        "source_class": source
        if isinstance(source, str) and source in {"none", "authored-ass", "converted"}
        else "unknown",
        "fields": {name: _safe_field(name, values.get(name)) for name in sorted(OPTIONS)},
    }


def _owner(raw: object) -> dict:
    if not isinstance(raw, dict) or count(raw.get("owner")) is None:
        return {"status": "invalid"}
    rows = raw.get("configurations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        return {"status": "invalid"}
    result: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or count(row.get("revision")) is None:
            return {"status": "invalid"}
        result.append(_configuration(row))
    if len({row["revision"] for row in result}) != len(result):
        return {"status": "invalid"}
    return {
        "status": "collected",
        "owner": raw["owner"],
        "configurations": result,
        "closed": raw.get("closed") if type(raw.get("closed")) is bool else None,
        "configurations_evicted": count(raw.get("configurations_evicted")),
    }


def safe_snapshot(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        return {"status": "unsupported-schema"}
    owners = raw.get("owners")
    if not isinstance(owners, list) or len(owners) > 4:
        return {"status": "invalid"}
    if not owners:
        return {"status": "unknown"}
    sanitized = [_owner(owner) for owner in owners]
    identifiers = [owner.get("owner") for owner in sanitized]
    if None in identifiers or len(set(identifiers)) != len(identifiers):
        return {"status": "invalid"}
    return {
        "status": "partial",
        "schema": 1,
        "clock": "unix-nanoseconds",
        "origin": "native-geometry-property-reader",
        "scope": "last consumed option values; not an atomic player snapshot or applied state",
        "query_error": "unknown",
        "profile_cli_origin": "unknown",
        "applied": "unknown",
        "owners": sanitized,
        "owners_evicted": count(raw.get("owners_evicted")),
    }


registry = EvidenceRegistry()


class PlayerEvidence:
    """Owned by native geometry's owner thread; the export sink locks its snapshots."""

    def __init__(self) -> None:
        self._registry = registry
        self.owner = self._registry.allocate()
        self.revision = 0
        self._key: object = None
        self._state: dict = {
            "owner": self.owner,
            "closed": False,
            "configurations": [],
            "configurations_evicted": 0,
        }

    def record(self, settings: Mapping[str, object], source: str) -> None:
        values = fields(settings)
        key = (values, source)
        if key == self._key:
            return
        self._key = key
        self.revision += 1
        rows = self._state["configurations"]
        rows.append(
            {
                "revision": self.revision,
                "captured_ns": time.time_ns(),
                "source_class": source,
                "fields": values,
            }
        )
        if len(rows) > 4:
            rows.pop(0)
            self._state["configurations_evicted"] += 1
        self._registry.update(self.owner, self._state)
        with otel_metrics.traced("player_configuration_read") as span:
            span.set("player_configuration_owner", self.owner)
            span.set("player_configuration_revision", self.revision)

    def close(self) -> None:
        self._state["closed"] = True
        if self.revision:
            self._registry.update(self.owner, self._state)
