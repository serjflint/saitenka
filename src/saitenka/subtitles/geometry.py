"""Provider-neutral subtitle geometry contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from saitenka.subtitles.document import SubtitleEventId, SubtitleTrackId


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True, slots=True)
class TokenGeometry:
    """Geometry for one semantic token; ``bounds`` is the stable UI anchor."""

    token_index: int
    bounds: Rect
    regions: tuple[Rect, ...] = ()


class GeometryVariant(StrEnum):
    NATIVE = "native"
    STYLED = "styled"
    HIT_MAP = "geometry-map"


@dataclass(frozen=True, slots=True, order=True)
class GeometryPaletteEntry:
    event_id: SubtitleEventId
    token_index: int
    rgb: int

    def __post_init__(self) -> None:
        if self.token_index < 0 or isinstance(self.rgb, bool) or not 0 < self.rgb <= 0xFFFFFF:
            raise ValueError("geometry palette entries require a token index and 24-bit RGB")


def _validate_render_space(request: GeometryRequest) -> None:
    if request.timestamp_ms < 0:
        raise ValueError("geometry timestamp must be non-negative")
    if any(value <= 0 for size in (request.frame_size, request.storage_size) for value in size):
        raise ValueError("geometry frame and storage sizes must be positive")
    if not math.isfinite(request.pixel_aspect) or request.pixel_aspect <= 0:
        raise ValueError("geometry pixel aspect must be finite and positive")


def _validate_palette(request: GeometryRequest) -> None:
    if len({entry.rgb for entry in request.palette}) != len(request.palette):
        raise ValueError("geometry palette colors must be unique")
    identities = {(entry.event_id, entry.token_index) for entry in request.palette}
    if len(identities) != len(request.palette):
        raise ValueError("geometry palette token identities must be unique")
    if any(entry.event_id != request.event_id for entry in request.palette):
        raise ValueError("geometry palette entries must belong to the requested event")
    reserved = set(request.reserved_rgb)
    if any(isinstance(color, bool) or not 0 <= color <= 0xFFFFFF for color in reserved):
        raise ValueError("reserved geometry colors must be 24-bit RGB")
    if reserved & {entry.rgb for entry in request.palette}:
        raise ValueError("geometry palette colors must not be reserved")


def _validate_attachments(request: GeometryRequest) -> None:
    names = [name for name, _data in request.attachments]
    if any(not name or "\x00" in name for name in names) or len(names) != len(set(names)):
        raise ValueError("geometry attachment names must be non-empty and unique")
    profile_names = [name for name, _value in request.render_profile]
    if any(not name for name in profile_names) or len(profile_names) != len(set(profile_names)):
        raise ValueError("geometry render profile names must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class GeometryRequest:
    generation: int
    track_id: SubtitleTrackId
    event_id: SubtitleEventId
    timestamp_ms: int
    frame_size: tuple[int, int]
    storage_size: tuple[int, int]
    ass: bytes
    variant: GeometryVariant = GeometryVariant.HIT_MAP
    pixel_aspect: float = 1.0
    palette: tuple[GeometryPaletteEntry, ...] = ()
    reserved_rgb: tuple[int, ...] = ()
    attachments: tuple[tuple[str, bytes], ...] = ()
    render_profile: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _validate_render_space(self)
        _validate_palette(self)
        _validate_attachments(self)

    def cache_key(self) -> str:
        """Stable identity for render inputs; generation is deliberately excluded."""
        digest = hashlib.sha256()
        for value in (
            str(self.track_id),
            repr(self.event_id),
            str(self.timestamp_ms),
            repr(self.frame_size),
            repr(self.storage_size),
            self.variant.value,
            repr(self.pixel_aspect),
            repr(self.palette),
            repr(self.reserved_rgb),
            repr(self.render_profile),
        ):
            digest.update(value.encode())
            digest.update(b"\0")
        digest.update(self.ass)
        for name, data in self.attachments:
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(data)
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class GeometrySnapshot:
    generation: int
    track_id: SubtitleTrackId
    event_id: SubtitleEventId
    timestamp_ms: int
    variant: GeometryVariant
    tokens: tuple[TokenGeometry, ...]


class GeometryBackend(Protocol):
    """Synchronous worker-side geometry provider; orchestration lives in the app."""

    def render(self, request: GeometryRequest) -> GeometrySnapshot: ...

    def close(self) -> None: ...
