"""Generation-safe lifecycle for subtitle geometry providers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka import otel_metrics
from saitenka.app.subtitle_geometry_diagnostics import geometry_error_code
from saitenka.app.subtitle_ownership import ASK_MPV, SelectedSid

if TYPE_CHECKING:
    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import DrawResult
    from saitenka.subtitles.geometry import GeometryBackend, GeometryRequest, GeometrySnapshot


class CurrentSubtitleRenderer(Protocol):
    """One member per renderer, so the widest of them sets the signature for all.

    That is precisely why the draw members take a request rather than a host: while `draw` took a
    `Reader`, the native renderer could never be narrower than the legacy one it shares this
    protocol with.

    **Total, deliberately.** The coordinator used to probe every lifecycle member with `getattr`
    and skip it when absent, which no type checker can see: a renamed method, or a renderer that
    never grew one, read as "this renderer does not do that" — a silent no-op indistinguishable
    from a deliberate one. Every renderer answers every member now, and a renderer with no pixel
    ownership to defend answers by doing nothing *on purpose*.
    """

    def draw(
        self, request, surfaces=None, ipc=None, /, *, on_settled=None
    ) -> DrawResult | None: ...

    def clear(self, surfaces=None, ipc=None, /) -> None: ...

    def close(self) -> None: ...

    @property
    def logged_first(self) -> bool:
        """Whether a first-subtitle line has already been logged, for the caller to carry back."""
        ...

    def activate(self, reader: Reader, sid: SelectedSid = ASK_MPV, /) -> bool:
        """Take the pixels, idempotently. `False` means the caller must draw them itself.

        Idempotent by contract: safe to call on any event without tracking whether it already did.
        It absorbed the old `reassert`, and the precondition separating them — has the ground moved
        under the established flag? — is *mostly* the renderer's own state. The exception is the
        selection: a caller that has just written `sid` knows the new track and the renderer does
        not, because mpv echoes the property asynchronously. That caller declares it.
        """
        ...

    def deactivate(self, reader: Reader, /) -> None:
        """Give the pixels back, at close."""
        ...

    def suspend_for_overlay(self, reader: Reader, /) -> None: ...

    def resume_after_overlay(self, reader: Reader, /) -> None: ...

    def cue_changed(self, reader: Reader, /, *, nonempty: bool) -> None: ...

    def connection_replaced(self, reader: Reader, /) -> None: ...

    def degrade_geometry(self, reader: Reader, /) -> None: ...

    def use_native(self, reader: Reader, /) -> bool:
        """Whether native geometry may be used. A renderer with no native pixel path answers
        `True`: it has no ownership to prove, so it never withholds geometry."""
        ...


@dataclass(frozen=True, slots=True)
class GeometryTicket:
    sequence: int
    request: GeometryRequest


@dataclass(frozen=True, slots=True)
class GeometryReservation:
    sequence: int
    generation: int


@dataclass(frozen=True, slots=True)
class GeometryResolution:
    snapshot: GeometrySnapshot | None
    failure_recorded: bool = False


@dataclass(frozen=True, slots=True)
class GeometryPrefetchResolution:
    snapshot: GeometrySnapshot | None
    error: Exception | None = None


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
        """The one place that turns a host into a draw request and the result back into host state.

        Was spread across each renderer's `draw`. Collapsing it here is what makes the renderers
        host-free: they share one protocol member, so the widest of them set the signature for all.
        """
        from saitenka.app.subtitle_render import build_draw_request

        self._renderer.activate(reader)
        result = self._renderer.draw(
            build_draw_request(reader), reader.lifecycle_surfaces, reader.ipc
        )
        if result is None:
            return
        # Returned, not assigned by the renderer: the boxes and origin belong to the cue that
        # produced them, and writing them onto a host mid-render outlives a superseded cue.
        reader.boxes = result.boxes
        reader.sub_origin = result.origin
        reader._first_sub_logged = self._renderer.logged_first

    def clear(self, reader: Reader) -> None:
        self._renderer.clear(reader.lifecycle_surfaces, reader.ipc)

    def activate(self, reader: Reader, sid: SelectedSid = ASK_MPV) -> None:
        if self._renderer.activate(reader, sid) is False:
            self.draw_current(reader)

    def geometry_degraded(self, reader: Reader) -> None:
        self._renderer.degrade_geometry(reader)

    def cue_changed(self, reader: Reader, *, nonempty: bool) -> None:
        self._renderer.cue_changed(reader, nonempty=nonempty)

    def deactivate(self, reader: Reader) -> None:
        self._renderer.deactivate(reader)

    def suspend_for_overlay(self, reader: Reader) -> None:
        self._renderer.suspend_for_overlay(reader)

    def resume_after_overlay(self, reader: Reader) -> None:
        self._renderer.resume_after_overlay(reader)

    def connection_replaced(self, reader: Reader) -> None:
        self._renderer.connection_replaced(reader)

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

    def consume_error(self) -> str | None:
        with self._state_lock:
            error = self._last_error
            self._last_error = None
            return error

    def invalidate(self) -> int:
        with self._state_lock:
            if self._closed:
                return self._generation
            self._generation += 1
            self._current = None
            self._last_error = None
            return self._generation

    def render(self, request: GeometryRequest) -> GeometrySnapshot | None:
        ticket = self.prepare(request)
        if ticket is None:
            return None
        return self.resolve(ticket)

    def resolve(self, ticket: GeometryTicket) -> GeometrySnapshot | None:
        return self.resolve_outcome(ticket).snapshot

    def resolve_outcome(self, ticket: GeometryTicket) -> GeometryResolution:
        request = ticket.request
        with otel_metrics.traced("subtitle_geometry_render") as span:
            span.set("active_events", len(request.frame_id.active_event_ids))
            span.set("requested_tokens", len(request.palette))
            span.set("frame_width", request.frame_size[0])
            span.set("frame_height", request.frame_size[1])
            with self._backend_lock:
                with self._state_lock:
                    if self._closed or ticket.sequence != self._request_sequence:
                        span.set("outcome", "superseded")
                        return GeometryResolution(None)
                if self._backend is None:
                    span.set("outcome", "unavailable")
                    return GeometryResolution(None)
                try:
                    result = self._backend.render(request)
                except Exception as error:  # noqa: BLE001 -- optional provider boundary
                    span.set("outcome", "failed")
                    span.set("error_code", geometry_error_code(error))
                    reservation = GeometryReservation(ticket.sequence, request.generation)
                    return GeometryResolution(None, self.record_error(reservation, error))
            published = self.publish(ticket, result)
            span.set("outcome", "ready" if published else "superseded")
            span.set("found_tokens", len(result.tokens))
            return GeometryResolution(result if published else None)

    def prepare(self, request: GeometryRequest) -> GeometryTicket | None:
        reservation = self.reserve(request.generation)
        return None if reservation is None else self.bind(reservation, request)

    def reserve(self, generation: int) -> GeometryReservation | None:
        with self._state_lock:
            if self._closed or generation != self._generation:
                return None
            self._request_sequence += 1
            return GeometryReservation(self._request_sequence, generation)

    def bind(
        self,
        reservation: GeometryReservation,
        request: GeometryRequest,
    ) -> GeometryTicket | None:
        with self._state_lock:
            if (
                self._closed
                or reservation.sequence != self._request_sequence
                or reservation.generation != self._generation
                or request.generation != reservation.generation
            ):
                return None
            return GeometryTicket(reservation.sequence, request)

    def publish(self, ticket: GeometryTicket, result: GeometrySnapshot) -> bool:
        request = ticket.request
        if (
            result.generation != request.generation
            or result.track_id != request.track_id
            or result.frame_id != request.frame_id
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

    def record_error(self, reservation: GeometryReservation, error: Exception) -> bool:
        with self._state_lock:
            if (
                self._closed
                or reservation.sequence != self._request_sequence
                or reservation.generation != self._generation
            ):
                return False
            self._last_error = str(error)
            self._current = None
            return True

    def render_prefetch(self, request: GeometryRequest) -> GeometrySnapshot | None:
        return self.render_prefetch_outcome(request).snapshot

    def render_prefetch_outcome(self, request: GeometryRequest) -> GeometryPrefetchResolution:
        with self._backend_lock:
            with self._state_lock:
                if self._closed or request.generation != self._generation:
                    return GeometryPrefetchResolution(None)
            if self._backend is None:
                return GeometryPrefetchResolution(None)
            try:
                return GeometryPrefetchResolution(self._backend.render(request))
            except Exception as error:  # noqa: BLE001 -- caller decides whether work has a waiter
                return GeometryPrefetchResolution(None, error)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._current = None
        # The renderer is a close participant alongside the geometry backend: quarantining geometry
        # while the raster surface stays live leaves a late annotation able to publish pixels.
        self._renderer.close()
        with self._backend_lock:
            if self._backend is not None:
                self._backend.close()
