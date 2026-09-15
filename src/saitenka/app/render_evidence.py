"""Bounded, text-free evidence from the native geometry request/publication boundary."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka_subtitles.geometry import GeometryRequest, GeometrySnapshot

_OWNERS = 4
_CONFIGURATIONS = 4
_SOURCE_DECISIONS = 32
_SOURCE_NAMES = frozenset({"auto", "mpv", "shadow", "legacy", "none"})
_SOURCE_REASONS = frozenset(
    {
        "unprobed",
        "invalidated",
        "fetching",
        "collection-ready",
        "scan-only",
        "stale",
        "unsupported-api",
        "layout-unsupported-render-mode",
        "layout-unavailable",
        "layout-event-count",
        "layout-geometry-profile",
        "layout-margins",
        "unbound-event",
        "configured-shadow",
        "legacy",
        "disabled-externally",
        "unsupported-option",
        "ipc-failed",
        "ipc-unavailable",
        "unmapped-tokens",
        "waiting-for-native-owner",
    }
)


def _source_record(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, object] = {}
    for key in ("configured_source", "selected_source", "scan_source", "paint_source"):
        value = raw.get(key)
        result[key] = value if isinstance(value, str) and value in _SOURCE_NAMES else "unknown"
    reason = raw.get("reason")
    result["reason"] = reason if isinstance(reason, str) and reason in _SOURCE_REASONS else "other"
    paint_reason = raw.get("paint_reason")
    result["paint_reason"] = (
        paint_reason
        if isinstance(paint_reason, str)
        and paint_reason
        in {"eligible", "no-scan-regions", "scan-only-policy", "shadow-paint-unqualified"}
        else "unknown"
    )
    result["paint_allowed"] = (
        raw.get("paint_allowed") if type(raw.get("paint_allowed")) is bool else None
    )
    for key in ("cue_revision", "generation", "eligible_tokens", "captured_ns"):
        result[key] = _count(raw.get(key))
    return result


def _source_history(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    history = raw.get("history")
    if not isinstance(history, list) or len(history) > _SOURCE_DECISIONS:
        return {"status": "invalid"}
    return {
        "status": "partial",
        "scope": "geometry eligibility; not displayed pixels",
        "history": [_source_record(row) for row in history],
        "evicted": _count(raw.get("evicted")),
    }


VALIDATION_VERDICTS = (
    "mask-exact",
    "probe-error",
    "probe-budget-exceeded",
    "unsupported-text",
    "exact-mask-mismatch",
    "unvalidated",
)


def validation_summary(snapshot: GeometrySnapshot) -> dict:
    return {
        "tokens": len(snapshot.tokens),
        "retained_mask_tokens": sum(bool(token.coverage) for token in snapshot.tokens),
        "evicted_mask_tokens": sum(token.coverage_evicted for token in snapshot.tokens),
        "verdicts": {
            verdict: sum(token.overprint_verdict == verdict for token in snapshot.tokens)
            for verdict in VALIDATION_VERDICTS
        },
    }


def _validation(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    verdicts = raw.get("verdicts")
    verdicts = verdicts if isinstance(verdicts, dict) else {}
    return {
        "scope": "same-renderer eligibility and retained masks; not uploaded pixels",
        **{
            key: _count(raw.get(key))
            for key in ("tokens", "retained_mask_tokens", "evicted_mask_tokens")
        },
        "verdicts": {key: _count(verdicts.get(key)) for key in VALIDATION_VERDICTS},
    }


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


_DOCUMENT_FIELDS = frozenset(
    {
        "summary_version",
        "style_count",
        "active_event_count",
        "playresx",
        "playresy",
        "layoutresx",
        "layoutresy",
        "wrapstyle",
    }
    | {
        f"tag_{tag}"
        for tag in (
            "pos",
            "move",
            "org",
            "clip",
            "iclip",
            "t",
            "fad",
            "fade",
            "fsp",
            "fscx",
            "fscy",
            "fn",
            "fs",
            "b",
            "i",
            "k",
            "kf",
            "ko",
            "p",
            "bord",
            "shad",
            "blur",
        )
    }
)


def _document_fields(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {key: _count(raw.get(key)) for key in sorted(_DOCUMENT_FIELDS)}


def _source_kind(value: object) -> str:
    return value if isinstance(value, str) and value in {"authored-ass", "converted"} else "unknown"


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
        "document": _document_fields(dict(request.document_metadata)),
        "source_kind": _source_kind(request.source_kind),
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
    result["document"] = _document_fields(raw.get("document"))
    result["source_kind"] = _source_kind(raw.get("source_kind"))
    return result


def _reference(raw: object, revisions: set[int], *, generation: int | None = None) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    values = {key: _count(raw.get(key)) for key in ("revision", "generation", "sequence")}
    if any(value is None for value in values.values()):
        return {"status": "invalid"}
    if generation is not None and values["generation"] != generation:
        return {**values, "status": "stale"}
    version = _count(raw.get("libass_version"))
    runtime: dict = {"libass_version": version} if version is not None else {}
    mask_source = raw.get("mask_source")
    if isinstance(mask_source, str) and mask_source in {
        "native-original",
        "request-document",
        "unknown",
    }:
        runtime["mask_source"] = mask_source
    if "validation" in raw:
        runtime["validation"] = _validation(raw["validation"])
    return {
        **values,
        **runtime,
        "status": "retained" if values["revision"] in revisions else "evicted",
    }


def _selection(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"status": "unknown"}
    rows = raw.get("history")
    if not isinstance(rows, list) or len(rows) > _CONFIGURATIONS:
        return {"status": "invalid"}
    history = []
    for row in rows:
        if not isinstance(row, dict):
            return {"status": "invalid"}
        owner = row.get("pixel_owner")
        history.append(
            {
                "revision": _count(row.get("revision")),
                "captured_ns": _count(row.get("captured_ns")),
                "pixel_owner": owner
                if isinstance(owner, str) and owner in {"unknown", "none", "native", "legacy"}
                else "unknown",
                "legacy_forced": row.get("legacy_forced")
                if type(row.get("legacy_forced")) is bool
                else None,
            }
        )
    return {
        "status": "partial",
        "scope": "post-draw ownership state; not pixel validation",
        "history": history,
        "evicted": _count(raw.get("evicted")),
    }


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
        "renderer_selection": _selection(raw.get("renderer_selection")),
        "geometry_sources": _source_history(raw.get("geometry_sources")),
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

    def __init__(self, *, max_owners: int = _OWNERS) -> None:
        if max_owners <= 0:
            raise ValueError("evidence owner bound must be positive")
        self._max_owners = max_owners
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
            if len(self._owners) > self._max_owners:
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
            "renderer_selection": {"history": [], "evicted": 0},
            "geometry_sources": {"history": [], "evicted": 0},
        }

    def selection(self, *, pixel_owner: str, legacy_forced: bool) -> None:
        selection = self._state["renderer_selection"]
        history = selection["history"]
        if history and (history[-1]["pixel_owner"], history[-1]["legacy_forced"]) == (
            pixel_owner,
            legacy_forced,
        ):
            return
        history.append(
            {
                "revision": selection["evicted"] + len(history) + 1,
                "captured_ns": time.time_ns(),
                "pixel_owner": pixel_owner,
                "legacy_forced": legacy_forced,
            }
        )
        if len(history) > _CONFIGURATIONS:
            history.pop(0)
            selection["evicted"] += 1
        self._registry.update(self.owner, self._state)

    def geometry_source(self, record: dict[str, str | int | bool]) -> None:
        evidence = self._state["geometry_sources"]
        evidence["history"].append(_source_record({**record, "captured_ns": time.time_ns()}))
        if len(evidence["history"]) > _SOURCE_DECISIONS:
            evidence["history"].pop(0)
            evidence["evicted"] += 1
        self._registry.update(self.owner, self._state)

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

    def published(
        self,
        revision: int,
        generation: int,
        sequence: int,
        *,
        libass_version: int | None = None,
        mask_source: str = "unknown",
        validation: dict | None = None,
    ) -> None:
        publication: dict[str, object] = {
            "revision": revision,
            "generation": generation,
            "sequence": sequence,
        }
        if libass_version is not None:
            publication["libass_version"] = libass_version
        if mask_source != "unknown":
            publication["mask_source"] = mask_source
        if validation is not None:
            publication["validation"] = _validation(validation)
        self._state["published"] = publication
        self._state["last_published"] = publication
        self._registry.update(self.owner, self._state)

    def clear_published(self) -> None:
        self._state["published"] = None
        self._registry.update(self.owner, self._state)

    def invalidate(self, generation: int, *, closed: bool = False) -> None:
        self._state.update(generation=generation, closed=closed, requested=None, published=None)
        if self._revision:
            self._registry.update(self.owner, self._state)
