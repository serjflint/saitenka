"""Generation-safe lifecycle for subtitle geometry providers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from saitenka.app.controller import Reader
    from saitenka.subtitles.geometry import GeometryBackend, GeometryRequest, GeometrySnapshot


class CurrentSubtitleRenderer(Protocol):
    def draw(self, reader: Reader) -> None: ...


@dataclass(frozen=True, slots=True)
class GeometryTicket:
    sequence: int
    request: GeometryRequest


class SubtitleModeCoordinator:
    """Reject obsolete geometry while keeping provider ownership out of ``Reader``."""

    def __init__(
        self,
        renderer: CurrentSubtitleRenderer,
        backend: GeometryBackend | None = None,
    ):
        self._renderer = renderer
        self._backend = backend
        self._state_lock = threading.Lock()
        self._backend_lock = threading.Lock()
        self._generation = 0
        self._request_sequence = 0
        self._current: GeometrySnapshot | None = None
        self._last_error: str | None = None
        self._closed = False

    @property
    def renderer(self) -> CurrentSubtitleRenderer:
        return self._renderer

    @renderer.setter
    def renderer(self, renderer: CurrentSubtitleRenderer) -> None:
        self._renderer = renderer

    def draw_current(self, reader: Reader) -> None:
        self._renderer.draw(reader)

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    @property
    def current(self) -> GeometrySnapshot | None:
        with self._state_lock:
            return self._current

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def invalidate(self) -> int:
        with self._state_lock:
            if self._closed:
                return self._generation
            self._generation += 1
            self._current = None
            return self._generation

    def render(self, request: GeometryRequest) -> GeometrySnapshot | None:
        ticket = self.prepare(request)
        if ticket is None:
            return None
        with self._backend_lock:
            with self._state_lock:
                if self._closed or ticket.sequence != self._request_sequence:
                    return None
            if self._backend is None:
                return None
            try:
                result = self._backend.render(request)
            except Exception as error:  # noqa: BLE001 -- an optional provider must fail back to mpv
                with self._state_lock:
                    if ticket.sequence == self._request_sequence:
                        self._last_error = str(error)
                return None
        return result if self.publish(ticket, result) else None

    def prepare(self, request: GeometryRequest) -> GeometryTicket | None:
        with self._state_lock:
            if self._closed or request.generation != self._generation:
                return None
            self._request_sequence += 1
            return GeometryTicket(self._request_sequence, request)

    def publish(self, ticket: GeometryTicket, result: GeometrySnapshot) -> bool:
        request = ticket.request
        if (
            result.generation != request.generation
            or result.track_id != request.track_id
            or result.event_id != request.event_id
            or result.timestamp_ms != request.timestamp_ms
            or result.variant != request.variant
        ):
            return False
        with self._state_lock:
            if (
                self._closed
                or ticket.sequence != self._request_sequence
                or result.generation != self._generation
            ):
                return False
            self._current = result
            self._last_error = None
            return True

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._current = None
        with self._backend_lock:
            if self._backend is not None:
                self._backend.close()
