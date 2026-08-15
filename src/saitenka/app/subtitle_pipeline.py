"""Generation-safe lifecycle for subtitle geometry providers."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Protocol

from saitenka.subtitles.geometry import GeometrySnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.subtitles.geometry import GeometryBackend, GeometryRequest

    GeometryRequestBuilder = Callable[[], GeometryRequest]


class CurrentSubtitleRenderer(Protocol):
    def draw(self, reader: Reader) -> None: ...


@dataclass(frozen=True, slots=True)
class GeometryTicket:
    sequence: int
    request: GeometryRequest


@dataclass(frozen=True, slots=True)
class GeometryReservation:
    sequence: int
    generation: int


@dataclass(frozen=True, slots=True)
class GeometryWorkerStats:
    submitted: int
    superseded: int
    completed: int
    cache_hits: int
    failures: int
    ready_before_presented: int
    presented: int
    max_submit_us: int
    prefetched: int
    prefetch_dropped: int
    result_cache_entries: int
    prefetch_cache_entries: int


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

    def activate(self, reader: Reader) -> None:
        activate = getattr(self._renderer, "activate", None)
        if activate is not None:
            activate(reader)

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
        return self.resolve(ticket)

    def resolve(self, ticket: GeometryTicket) -> GeometrySnapshot | None:
        request = ticket.request
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
        with self._backend_lock:
            with self._state_lock:
                if self._closed or request.generation != self._generation:
                    return None
            if self._backend is None:
                return None
            try:
                return self._backend.render(request)
            except Exception:  # noqa: BLE001 -- speculative work never changes visible state
                return None

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


class SubtitleGeometryWorker:
    """One worker, one pending slot, and a bounded result cache."""

    def __init__(self, coordinator: SubtitleModeCoordinator, *, cache_max: int = 3) -> None:
        if cache_max <= 0:
            raise ValueError("geometry result cache bound must be positive")
        self._coordinator = coordinator
        self._cache_max = cache_max
        self._cache: OrderedDict[str, GeometrySnapshot] = OrderedDict()
        self._condition = threading.Condition()
        self._pending: tuple[GeometryReservation, GeometryRequestBuilder] | None = None
        self._prefetch_pending: OrderedDict[str, tuple[int, GeometryRequestBuilder]] = OrderedDict()
        self._prefetched: OrderedDict[str, tuple[GeometryRequest, GeometrySnapshot]] = OrderedDict()
        self._busy = False
        self._closed = False
        self._submitted = 0
        self._superseded = 0
        self._completed = 0
        self._cache_hits = 0
        self._failures = 0
        self._ready_before_presented = 0
        self._presented = 0
        self._max_submit_us = 0
        self._prefetched_count = 0
        self._prefetch_dropped = 0
        self._thread = threading.Thread(
            target=self._run,
            name="saitenka-subtitle-geometry",
            daemon=True,
        )
        self._thread.start()

    def submit(self, request: GeometryRequest) -> bool:
        return self.submit_job(request.generation, lambda: request)

    def submit_job(self, generation: int, build: GeometryRequestBuilder) -> bool:
        started = time.perf_counter_ns()
        reservation = self._coordinator.reserve(generation)
        if reservation is None:
            return False
        with self._condition:
            if self._closed:
                return False
            self._submitted += 1
            if self._pending is not None:
                self._superseded += 1
            self._pending = (reservation, build)
            self._condition.notify()
            elapsed_us = (time.perf_counter_ns() - started + 999) // 1_000
            self._max_submit_us = max(self._max_submit_us, elapsed_us)
        return True

    def prefetch(self, key: str, generation: int, build: GeometryRequestBuilder) -> bool:
        if generation != self._coordinator.generation:
            return False
        with self._condition:
            if self._closed or key in self._prefetched:
                return False
            self._prefetch_pending.pop(key, None)
            self._prefetch_pending[key] = (generation, build)
            while len(self._prefetch_pending) > self._cache_max:
                self._prefetch_pending.popitem(last=False)
                self._prefetch_dropped += 1
            self._condition.notify()
            return True

    def publish_prefetched(self, key: str, generation: int) -> GeometryRequest | None:
        reservation = self._coordinator.reserve(generation)
        if reservation is None:
            return None
        with self._condition:
            cached = self._prefetched.pop(key, None)
            if cached is None:
                return None
            self._prefetched[key] = cached
        request, result = cached
        rebound_request = dataclass_replace(request, generation=generation)
        rebound_result = dataclass_replace(result, generation=generation)
        ticket = self._coordinator.bind(reservation, rebound_request)
        if ticket is None or not self._coordinator.publish(ticket, rebound_result):
            return None
        with self._condition:
            self._cache_hits += 1
            self._completed += 1
        return rebound_request

    def invalidate_cache(self) -> None:
        with self._condition:
            self._cache.clear()
            self._prefetched.clear()
            self._prefetch_pending.clear()

    def _cached(self, request: GeometryRequest) -> GeometrySnapshot | None:
        key = request.cache_key()
        with self._condition:
            result = self._cache.pop(key, None)
            if result is None:
                return None
            self._cache[key] = result
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.event_id,
            request.timestamp_ms,
            request.variant,
            result.tokens,
        )

    def _store(self, request: GeometryRequest, result: GeometrySnapshot) -> None:
        key = request.cache_key()
        with self._condition:
            self._cache.pop(key, None)
            self._cache[key] = result
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

    def _idle(self) -> None:
        self._busy = False
        self._condition.notify_all()

    def _drop_prefetch(self) -> None:
        with self._condition:
            self._prefetch_dropped += 1
            self._idle()

    def _process_prefetch(self, item: tuple[str, tuple[int, GeometryRequestBuilder]]) -> None:
        key, (generation, build) = item
        try:
            request = build()
        except Exception:  # noqa: BLE001 -- speculative failure is only a cache miss
            self._drop_prefetch()
            return
        if generation != self._coordinator.generation:
            self._drop_prefetch()
            return
        result = self._coordinator.render_prefetch(request)
        with self._condition:
            if result is None:
                self._prefetch_dropped += 1
            else:
                self._prefetched.pop(key, None)
                self._prefetched[key] = (request, result)
                self._store(request, result)
                self._prefetched_count += 1
                while len(self._prefetched) > self._cache_max:
                    self._prefetched.popitem(last=False)
            self._idle()

    def _finish_current(self, *, published: bool) -> None:
        with self._condition:
            if published:
                self._completed += 1
            elif self._coordinator.last_error is not None:
                self._failures += 1
            else:
                self._superseded += 1
            self._idle()

    def _finish_build_error(self, *, recorded: bool) -> None:
        with self._condition:
            if recorded:
                self._failures += 1
            else:
                self._superseded += 1
            self._idle()

    def _process_current(self, item: tuple[GeometryReservation, GeometryRequestBuilder]) -> None:
        reservation, build = item
        try:
            request = build()
        except Exception as error:  # noqa: BLE001 -- source preparation is optional
            self._finish_build_error(recorded=self._coordinator.record_error(reservation, error))
            return
        ticket = self._coordinator.bind(reservation, request)
        if ticket is None:
            self._finish_current(published=False)
            return
        cached = self._cached(ticket.request)
        if cached is not None:
            published = self._coordinator.publish(ticket, cached)
            with self._condition:
                self._cache_hits += 1
        else:
            result = self._coordinator.resolve(ticket)
            published = result is not None
            if result is not None:
                self._store(ticket.request, result)
        self._finish_current(published=published)

    def _next_work(
        self,
    ) -> tuple[
        tuple[GeometryReservation, GeometryRequestBuilder] | None,
        tuple[str, tuple[int, GeometryRequestBuilder]] | None,
    ]:
        with self._condition:
            while self._pending is None and not self._prefetch_pending and not self._closed:
                self._condition.wait()
            if self._closed:
                return None, None
            pending = self._pending
            if pending is not None:
                self._pending = None
                prefetch = None
            else:
                prefetch = self._prefetch_pending.popitem(last=False)
            self._busy = True
            return pending, prefetch

    def _run(self) -> None:
        while True:
            pending, prefetch = self._next_work()
            if self._closed:
                return
            if pending is not None:
                self._process_current(pending)
            else:
                assert prefetch is not None
                self._process_prefetch(prefetch)

    def mark_presented(self, request: GeometryRequest) -> bool:
        current = self._coordinator.current
        ready = current is not None and (
            current.generation,
            current.track_id,
            current.event_id,
            current.timestamp_ms,
            current.variant,
        ) == (
            request.generation,
            request.track_id,
            request.event_id,
            request.timestamp_ms,
            request.variant,
        )
        with self._condition:
            self._presented += 1
            self._ready_before_presented += int(ready)
        return ready

    def mark_not_ready(self) -> None:
        with self._condition:
            self._presented += 1

    @property
    def stats(self) -> GeometryWorkerStats:
        with self._condition:
            return GeometryWorkerStats(
                self._submitted,
                self._superseded,
                self._completed,
                self._cache_hits,
                self._failures,
                self._ready_before_presented,
                self._presented,
                self._max_submit_us,
                self._prefetched_count,
                self._prefetch_dropped,
                len(self._cache),
                len(self._prefetched),
            )

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._pending is not None or self._prefetch_pending or self._busy
            ) and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return self._pending is None and not self._prefetch_pending and not self._busy

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._prefetch_pending.clear()
            self._prefetched.clear()
            self._cache.clear()
            self._condition.notify_all()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("subtitle geometry worker did not stop")
        self._coordinator.close()
