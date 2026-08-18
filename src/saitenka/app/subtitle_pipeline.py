"""Generation-safe lifecycle for subtitle geometry providers."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Protocol

from saitenka import otel_metrics
from saitenka.app.subtitle_geometry_diagnostics import geometry_error_code
from saitenka.runtime import EffectFinished, Owner
from saitenka.runtime.jobs import JobLanePolicy, LocalJobLane
from saitenka.subtitles.geometry import GeometrySnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.controller import Reader
    from saitenka.subtitles.geometry import GeometryBackend, GeometryRequest

    GeometryRequestBuilder = Callable[[], GeometryRequest]


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


class CurrentSubtitleRenderer(Protocol):
    def draw(self, reader: Reader) -> object: ...

    def clear(self, reader: Reader) -> None: ...

    def close(self) -> None: ...


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

    def clear(self, reader: Reader) -> None:
        self._renderer.clear(reader)

    def activate(self, reader: Reader) -> None:
        activate = getattr(self._renderer, "reassert", None) or getattr(
            self._renderer, "activate", None
        )
        if activate is not None and activate(reader) is False:
            self._renderer.draw(reader)

    def geometry_degraded(self, reader: Reader) -> None:
        degrade = getattr(self._renderer, "degrade_geometry", None)
        if degrade is not None:
            degrade(reader)

    def cue_changed(self, reader: Reader, *, nonempty: bool) -> None:
        changed = getattr(self._renderer, "cue_changed", None)
        if changed is not None:
            changed(reader, nonempty=nonempty)

    def deactivate(self, reader: Reader) -> None:
        deactivate = getattr(self._renderer, "deactivate", None)
        if deactivate is not None:
            deactivate(reader)

    def suspend_for_overlay(self, reader: Reader) -> None:
        suspend = getattr(self._renderer, "suspend_for_overlay", None)
        if suspend is not None:
            suspend(reader)

    def resume_after_overlay(self, reader: Reader) -> None:
        resume = getattr(self._renderer, "resume_after_overlay", None)
        if resume is not None:
            resume(reader)

    def connection_replaced(self, reader: Reader) -> None:
        replaced = getattr(self._renderer, "connection_replaced", None)
        if replaced is not None:
            replaced(reader)

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


GEOMETRY_LANE = "subtitle-geometry"

#: Deeper than the one job in flight: `_pump` re-submits from the tail of the handler, while the
#: finished effect is still counted against the lane, and a refused admission is dropped work.
_GEOMETRY_LANE_POLICY = JobLanePolicy(capacity=4, workers=1)


@dataclass(frozen=True, slots=True)
class GeometryJob:
    """One unit of lane work: either the current request or one speculative prefetch."""

    worker: SubtitleGeometryWorker
    current: tuple[GeometryReservation, GeometryRequestBuilder, str | None] | None
    prefetch: tuple[str, tuple[int, GeometryRequestBuilder]] | None


def run_geometry_job(request: object, cancelled: threading.Event) -> object:
    _ = cancelled  # the coordinator's generation fence already retires superseded work
    if not isinstance(request, GeometryJob):
        raise TypeError("invalid subtitle-geometry request")
    request.worker.execute(request)
    return None


def configure_runtime_job(ipc) -> JobSubmitter | None:
    """Resolve the lane once, at composition. Absence becomes an explicit value here rather than a
    probe at each use site — see the sanctioned half of the capability-probe split."""
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(GEOMETRY_LANE, _GEOMETRY_LANE_POLICY, run_geometry_job):
        return None
    return ipc.submit_runtime_job


class SubtitleGeometryWorker:
    """One pending slot, a bounded prefetch queue, and a bounded result cache.

    The queue policy is the feature's; execution is the lane's. Without a gateway the lane is local
    (`LocalJobLane`) rather than a private thread, so there is one execution path either way.
    """

    def __init__(
        self,
        coordinator: SubtitleModeCoordinator,
        *,
        cache_max: int = 3,
        submit: JobSubmitter | None = None,
    ) -> None:
        if cache_max <= 0:
            raise ValueError("geometry result cache bound must be positive")
        self._coordinator = coordinator
        self._cache_max = cache_max
        self._cache: OrderedDict[str, GeometrySnapshot] = OrderedDict()
        self._condition = threading.Condition()
        self._pending: tuple[GeometryReservation, GeometryRequestBuilder, str | None] | None = None
        self._prefetch_pending: OrderedDict[str, tuple[int, GeometryRequestBuilder]] = OrderedDict()
        self._prefetched: OrderedDict[str, tuple[GeometryRequest, GeometrySnapshot]] = OrderedDict()
        self._prefetch_inflight_key: str | None = None
        self._prefetch_waiters: dict[str, GeometryReservation] = {}
        self._provenance: OrderedDict[str, str] = OrderedDict()
        self._history_lossy = False
        self._epoch_cause: str | None = None
        self._inflight = False
        self._issued = 0
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
        self._local = (
            None
            if submit is not None
            else LocalJobLane(GEOMETRY_LANE, _GEOMETRY_LANE_POLICY, run_geometry_job)
        )
        self._submit: JobSubmitter = submit or self._local.submit  # type: ignore[union-attr]

    @property
    def generation(self) -> int:
        return self._coordinator.generation

    def submit(self, request: GeometryRequest) -> bool:
        return self.submit_job(request.generation, lambda: request)

    def submit_job(
        self,
        generation: int,
        build: GeometryRequestBuilder,
        *,
        work_key: str | None = None,
    ) -> bool:
        started = time.perf_counter_ns()
        reservation = self._coordinator.reserve(generation)
        if reservation is None:
            return False
        with self._condition:
            if self._closed:
                return False
            self._submitted += 1
            if work_key is not None and work_key == self._prefetch_inflight_key:
                if work_key in self._prefetch_waiters:
                    self._superseded += 1
                self._prefetch_waiters[work_key] = reservation
                self._record_submit_latency(started)
                return True
            if work_key is not None and work_key in self._prefetch_pending:
                # Promote the queued speculation instead of running its builder: the current
                # request already carries the real one.
                self._prefetch_pending.pop(work_key)
            if self._pending is not None:
                self._superseded += 1
            self._pending = (reservation, build, work_key)
            self._record_submit_latency(started)
        self._pump()
        return True

    def _record_submit_latency(self, started: int) -> None:
        elapsed_us = (time.perf_counter_ns() - started + 999) // 1_000
        self._max_submit_us = max(self._max_submit_us, elapsed_us)

    def prefetch(self, key: str, generation: int, build: GeometryRequestBuilder) -> bool:
        if generation != self._coordinator.generation:
            return False
        with self._condition:
            if self._closed or key in self._prefetched:
                return False
            self._prefetch_pending.pop(key, None)
            self._prefetch_pending[key] = (generation, build)
            while len(self._prefetch_pending) > self._cache_max:
                dropped, _item = self._prefetch_pending.popitem(last=False)
                self._prefetch_dropped += 1
                self._remember_provenance(dropped, "prefetch-superseded")
        self._pump()
        return True

    def _pump(self) -> None:
        """Hand the next queued item to the lane, one in flight at a time.

        The queue policy stays here — a single current slot that supersedes, a bounded prefetch
        queue that drops oldest — because the broker owns admission and never feature state.
        """
        with self._condition:
            if self._closed or self._inflight:
                return
            pending, self._pending = self._pending, None
            if pending is not None:
                job = GeometryJob(self, pending, None)
            elif self._prefetch_pending:
                entry = self._prefetch_pending.popitem(last=False)
                self._prefetch_inflight_key = entry[0]
                job = GeometryJob(self, None, entry)
            else:
                return
            self._inflight = True
            self._issued += 1
            identity = (GEOMETRY_LANE, self._issued)

        def finished(_completion: EffectFinished) -> None:
            """The terminal has no consumer yet: the host still polls `apply` from a tick stage.
            Routing that poll onto this completion is the next contract, and needs every
            geometry-owning test gateway-wired first."""

        if self._submit(
            owner=Owner.SUBTITLE,
            identity=identity,
            lane=GEOMETRY_LANE,
            request=job,
            on_finished=finished,
        ):
            return
        # Refused admission is dropped work, not queued work: unwind so the queue does not believe
        # something is still in flight and stall every later request behind it.
        with self._condition:
            self._inflight = False
            if job.current is not None:
                self._superseded += 1
            else:
                self._prefetch_inflight_key = None
                self._prefetch_dropped += 1
            self._condition.notify_all()

    def execute(self, job: GeometryJob) -> None:
        """Run one lane job. Called on a lane worker thread by :func:`run_geometry_job`."""
        try:
            if job.current is not None:
                self._process_current(job.current)
            else:
                assert job.prefetch is not None
                self._process_prefetch(job.prefetch)
        finally:
            self._pump()

    def _remember_provenance(self, key: str, reason: str) -> None:
        self._provenance.pop(key, None)
        self._provenance[key] = reason
        while len(self._provenance) > self._cache_max:
            self._provenance.popitem(last=False)
            self._history_lossy = True

    def prefetch_miss_reason(self, key: str) -> str:
        with self._condition:
            if key in self._prefetch_pending or key == self._prefetch_inflight_key:
                return "prefetch-pending"
            recent = self._provenance.get(key)
            if recent is not None:
                return recent
            if self._epoch_cause is not None:
                cause, self._epoch_cause = self._epoch_cause, None
                return cause
            return "provenance-unknown" if self._history_lossy else "first-seen"

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

    def invalidate_cache(self, *, cause: str | None = None) -> None:
        with self._condition:
            self._superseded += len(self._prefetch_waiters)
            self._cache.clear()
            self._prefetched.clear()
            self._prefetch_pending.clear()
            self._prefetch_waiters.clear()
            self._prefetch_inflight_key = None
            self._provenance.clear()
            self._history_lossy = False
            self._epoch_cause = cause

    def invalidate(self, *, cause: str | None = None) -> int:
        generation = self._coordinator.invalidate()
        self.invalidate_cache(cause=cause)
        return generation

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
            request.frame_id,
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
        self._inflight = False
        self._condition.notify_all()

    def _drop_prefetch(self, key: str, error: Exception | None = None) -> None:
        with self._condition:
            waiter = self._prefetch_waiters.pop(key, None)
            self._prefetch_inflight_key = None
            self._prefetch_dropped += 1
        failure_recorded = bool(
            waiter is not None
            and error is not None
            and self._coordinator.record_error(waiter, error)
        )
        with self._condition:
            if waiter is not None:
                if failure_recorded:
                    self._failures += 1
                else:
                    self._superseded += 1
            self._idle()

    def _process_prefetch(self, item: tuple[str, tuple[int, GeometryRequestBuilder]]) -> None:
        key, (generation, build) = item
        try:
            request = build()
        except Exception as error:  # noqa: BLE001 -- a promoted waiter turns this into a failure
            self._drop_prefetch(key, error)
            return
        if generation != self._coordinator.generation:
            self._drop_prefetch(key)
            return
        outcome = self._coordinator.render_prefetch_outcome(request)
        waiter = self._cache_prefetch_outcome(key, generation, request, outcome.snapshot)
        published, failure_recorded = self._resolve_prefetch_waiter(waiter, request, outcome)
        with self._condition:
            if waiter is not None:
                if published:
                    self._completed += 1
                    self._cache_hits += 1
                elif failure_recorded:
                    self._failures += 1
                else:
                    self._superseded += 1
            self._idle()

    def _cache_prefetch_outcome(
        self,
        key: str,
        generation: int,
        request: GeometryRequest,
        result: GeometrySnapshot | None,
    ) -> GeometryReservation | None:
        with self._condition:
            waiter = self._prefetch_waiters.pop(key, None)
            self._prefetch_inflight_key = None
            if result is None or generation != self._coordinator.generation:
                self._prefetch_dropped += 1
                return waiter
            self._prefetched.pop(key, None)
            self._prefetched[key] = (request, result)
            self._store(request, result)
            self._prefetched_count += 1
            while len(self._prefetched) > self._cache_max:
                evicted, _cached = self._prefetched.popitem(last=False)
                self._remember_provenance(evicted, "evicted")
            return waiter

    def _resolve_prefetch_waiter(
        self,
        waiter: GeometryReservation | None,
        request: GeometryRequest,
        outcome: GeometryPrefetchResolution,
    ) -> tuple[bool, bool]:
        if waiter is None:
            return False, False
        if outcome.error is not None:
            return False, self._coordinator.record_error(waiter, outcome.error)
        if outcome.snapshot is None:
            return False, False
        rebound_request = dataclass_replace(request, generation=waiter.generation)
        rebound_result = dataclass_replace(outcome.snapshot, generation=waiter.generation)
        ticket = self._coordinator.bind(waiter, rebound_request)
        return (
            ticket is not None and self._coordinator.publish(ticket, rebound_result),
            False,
        )

    def _finish_current(self, *, published: bool, failure_recorded: bool = False) -> None:
        with self._condition:
            if published:
                self._completed += 1
            elif failure_recorded:
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

    def _process_current(
        self, item: tuple[GeometryReservation, GeometryRequestBuilder, str | None]
    ) -> None:
        reservation, build, _work_key = item
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
            outcome = self._coordinator.resolve_outcome(ticket)
            published = outcome.snapshot is not None
            if outcome.snapshot is not None:
                self._store(ticket.request, outcome.snapshot)
            self._finish_current(
                published=published,
                failure_recorded=outcome.failure_recorded,
            )
            return
        self._finish_current(published=published)

    def mark_presented(self, request: GeometryRequest) -> bool:
        current = self._coordinator.current
        ready = current is not None and (
            current.generation,
            current.track_id,
            current.frame_id,
            current.timestamp_ms,
            current.variant,
        ) == (
            request.generation,
            request.track_id,
            request.frame_id,
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
        """Block until the queue is drained and no job is executing.

        This is about the *work*, not its terminal: the lane completion that publishes the result
        is drained by the runtime, so waiting on it here would be waiting on the caller.
        """
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._pending is not None or self._prefetch_pending or self._inflight
            ) and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return self._pending is None and not self._prefetch_pending and not self._inflight

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._prefetch_pending.clear()
            self._prefetch_waiters.clear()
            self._prefetch_inflight_key = None
            self._prefetched.clear()
            self._cache.clear()
            self._condition.notify_all()
        if self._local is not None:
            self._local.close()
        self._coordinator.close()
