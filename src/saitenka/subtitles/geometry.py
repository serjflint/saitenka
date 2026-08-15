"""Provider-neutral subtitle geometry contracts."""

from __future__ import annotations

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
