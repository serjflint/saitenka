"""The always-unavailable geometry provider.

Geometry is an optional capability: when no native renderer is installed the session must still
run, showing subtitle pixels with no hit boxes. This provider makes that the ordinary path rather
than a `None` special case threaded through every caller — it satisfies `GeometryBackend` and
answers every request with an empty, correctly-identified snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.subtitles.geometry import GeometrySnapshot

if TYPE_CHECKING:
    from saitenka.subtitles.geometry import GeometryRequest


class NullGeometryBackend:
    """Answers with no tokens. Interaction degrades; pixel ownership never changes."""

    def __init__(self) -> None:
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        if self._closed:
            raise RuntimeError("null geometry backend is closed")
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.frame_id,
            request.timestamp_ms,
            request.variant,
            (),
        )

    def close(self) -> None:
        self._closed = True
