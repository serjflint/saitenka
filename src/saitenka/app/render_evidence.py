"""Bounded, text-free evidence from the native geometry request/publication boundary."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka_subtitles.geometry import GeometryRequest

_OWNERS = 4
_CONFIGURATIONS = 4
_BOOL_FIELDS = frozenset(
    {
        "use_margins",
        "keep_coverage",
        "extract_fonts",
        "fonts_dir_configured",
        "default_font_configured",
        "default_family_configured",
        "fontconfig_configured",
        "selective_font_scale",
        "feature_wrap_unicode",
        "feature_bidi_brackets",
        "feature_whole_text_layout",
    }
)
_NUMBER_FIELDS = frozenset(
    {
        "frame_width",
        "frame_height",
        "storage_width",
        "storage_height",
        "pixel_aspect",
        "margin_top",
        "margin_bottom",
        "margin_left",
        "margin_right",
        "font_scale",
        "blur",
        "justify",
        "line_position",
        "line_spacing",
        "hinting",
        "font_provider",
        "attachment_count",
    }
)


def configuration_fields(request: GeometryRequest) -> dict:
    state, fonts = request.renderer_state, request.font_setup
    features = dict(state.features)
    return {
        "frame_width": request.frame_size[0],
        "frame_height": request.frame_size[1],
        "storage_width": request.storage_size[0],
        "storage_height": request.storage_size[1],
        "pixel_aspect": request.pixel_aspect,
        **dict(
            zip(
                ("margin_top", "margin_bottom", "margin_left", "margin_right"),
                request.margins,
                strict=True,
            )
        ),
        "use_margins": request.use_margins,
        "keep_coverage": request.keep_coverage,
        "font_scale": state.font_scale,
        "blur": state.blur,
        "justify": state.justify,
        "line_position": state.line_position,
        "line_spacing": state.line_spacing,
        "hinting": state.hinting,
        "selective_font_scale": state.selective_font_scale,
        "feature_wrap_unicode": features.get(3),
        "feature_bidi_brackets": features.get(1),
        "feature_whole_text_layout": features.get(2),
        "font_provider": int(fonts.font_provider),
        "extract_fonts": fonts.extract_fonts,
        "fonts_dir_configured": bool(fonts.fonts_dir),
        "default_font_configured": bool(fonts.default_font),
        "default_family_configured": bool(fonts.default_family),
        "fontconfig_configured": bool(fonts.fontconfig_config),
        "attachment_count": len(request.attachments),
    }


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _fields(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    result: dict = {}
    for key in sorted(_BOOL_FIELDS):
        value = raw.get(key)
        result[key] = value if type(value) is bool else None
    for key in sorted(_NUMBER_FIELDS):
        value = raw.get(key)
        result[key] = (
            value
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and abs(value) <= 1e9
                and math.isfinite(value)
            )
            else None
        )
    return result


def _reference(raw: object, revisions: set[int], *, generation: int | None = None) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    values = {key: _count(raw.get(key)) for key in ("revision", "generation", "sequence")}
    if any(value is None for value in values.values()):
        return {"status": "invalid"}
    if generation is not None and values["generation"] != generation:
        return {**values, "status": "stale"}
    return {**values, "status": "retained" if values["revision"] in revisions else "evicted"}


def _owner(raw: dict) -> dict:
    generation = _count(raw.get("generation"))
    if generation is None or _count(raw.get("owner")) is None:
        return {"status": "invalid"}
    configurations = raw.get("configurations")
    if not isinstance(configurations, list) or len(configurations) > _CONFIGURATIONS:
        return {"status": "invalid"}
    rows: list[dict] = []
    for row in configurations:
        if not isinstance(row, dict) or _count(row.get("revision")) is None:
            return {"status": "invalid"}
        rows.append(
            {
                "revision": row["revision"],
                "captured_ns": _count(row.get("captured_ns")),
                "fields": _fields(row.get("fields")),
            }
        )
    revisions = {row["revision"] for row in rows}
    if len(revisions) != len(rows):
        return {"status": "invalid"}
    return {
        "status": "collected",
        "owner": _count(raw.get("owner")),
        "closed": raw.get("closed") if type(raw.get("closed")) is bool else None,
        "generation": generation,
        "configurations": rows,
        "configurations_evicted": _count(raw.get("configurations_evicted")),
        "requested": _reference(raw.get("requested"), revisions, generation=generation),
        "published": _reference(raw.get("published"), revisions, generation=generation),
        "last_published": _reference(raw.get("last_published"), revisions),
    }


def safe_runtime_configuration(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        return {"status": "unsupported-schema"}
    owners = raw.get("owners")
    if (
        not isinstance(owners, list)
        or len(owners) > _OWNERS
        or any(not isinstance(row, dict) for row in owners)
    ):
        return {"status": "invalid"}
    if not owners:
        return {"status": "unknown"}
    owner_ids = [_count(row.get("owner")) for row in owners]
    if len(set(owner_ids)) != len(owner_ids):
        return {"status": "invalid"}
    return {
        "status": "partial",
        "schema": 1,
        "scope": "native geometry request inputs; not all effective player settings",
        "origin": "producer-geometry-boundary",
        "clock": "unix-nanoseconds",
        "publication_scope": "generation-fenced geometry; not displayed pixels or backend readback",
        "loaded_native_runtime": "unknown",
        "resolved_font_faces": "unknown",
        "font_content_identity": "unknown",
        "profile_cli_origin": "unknown",
        "owners_evicted": _count(raw.get("owners_evicted")),
        "owners": [_owner(row) for row in owners],
    }


class EvidenceRegistry:
    """A process-wide export sink; owners never overwrite another session's state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self._owners: OrderedDict[int, dict] = OrderedDict()
        self._evicted = 0

    def allocate(self) -> int:
        with self._lock:
            self._next += 1
            return self._next

    def update(self, owner: int, snapshot: dict) -> None:
        with self._lock:
            self._owners[owner] = deepcopy(snapshot)
            self._owners.move_to_end(owner)
            if len(self._owners) > _OWNERS:
                self._owners.popitem(last=False)
                self._evicted += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "schema": 1,
                "owners": deepcopy(list(self._owners.values())),
                "owners_evicted": self._evicted,
            }


registry = EvidenceRegistry()


class GeometryEvidence:
    """Serialized by the owning coordinator's state lock; no I/O or font hashing."""

    def __init__(self) -> None:
        self._registry = registry
        self.owner = self._registry.allocate()
        self._key: object = None
        self._revision = 0
        self._state: dict = {
            "owner": self.owner,
            "closed": False,
            "generation": 0,
            "configurations": [],
            "configurations_evicted": 0,
            "requested": None,
            "published": None,
            "last_published": None,
        }

    def describe(self, request: GeometryRequest) -> int:
        fields = _fields(configuration_fields(request))
        # Paths stay local, so equal public flags still distinguish a changed font configuration.
        key = (fields, request.font_setup)
        if key != self._key:
            self._key = key
            self._revision += 1
            rows = self._state["configurations"]
            rows.append(
                {"revision": self._revision, "captured_ns": time.time_ns(), "fields": fields}
            )
            if len(rows) > _CONFIGURATIONS:
                rows.pop(0)
                self._state["configurations_evicted"] += 1
        self._registry.update(self.owner, self._state)
        return self._revision

    def requested(self, revision: int, generation: int, sequence: int) -> None:
        self._state["generation"] = generation
        self._state["requested"] = {
            "revision": revision,
            "generation": generation,
            "sequence": sequence,
        }
        self._registry.update(self.owner, self._state)

    def published(self, revision: int, generation: int, sequence: int) -> None:
        self._state["published"] = {
            "revision": revision,
            "generation": generation,
            "sequence": sequence,
        }
        self._state["last_published"] = self._state["published"]
        self._registry.update(self.owner, self._state)

    def clear_published(self) -> None:
        self._state["published"] = None
        self._registry.update(self.owner, self._state)

    def invalidate(self, generation: int, *, closed: bool = False) -> None:
        self._state.update(generation=generation, closed=closed, requested=None, published=None)
        if self._revision:
            self._registry.update(self.owner, self._state)
