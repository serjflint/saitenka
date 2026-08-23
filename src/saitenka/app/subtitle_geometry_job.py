"""The subtitle-geometry lane: one queued job at a time, with a bounded prefetch and cache.

The generation fence it reserves against is `subtitle_pipeline.SubtitleModeCoordinator`: that
decides whether a result may still be published, this decides what runs and in what order.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, NamedTuple

from saitenka.app.subtitle_geometry_diagnostics import GeometryCacheReason
from saitenka.runtime import EffectFinished, Owner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, LocalJobLane, configure_lane
from saitenka.subtitles.geometry import GeometrySnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.subtitle_pipeline import (
        GeometryPrefetchResolution,
        GeometryReservation,
        SubtitleModeCoordinator,
    )
    from saitenka.subtitles.geometry import GeometryRequest

    GeometryRequestBuilder = Callable[[], GeometryRequest]


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
    coverage_trimmed: int = 0


GEOMETRY_LANE = "subtitle-geometry"

#: Bytes of token coverage kept across every cached and prefetched cue. The caches are bounded by
#: entries, and coverage is the one thing an entry count does not bound — a full-screen sign's alpha
#: is megabytes, and lookahead holds a window of them.
COVERAGE_BUDGET_BYTES = 16 * 1024 * 1024

#: Deeper than the one job in flight: `_pump` re-submits from the tail of the handler, while the
#: finished effect is still counted against the lane, and a refused admission is dropped work.
_GEOMETRY_LANE_POLICY = JobLanePolicy(capacity=4, workers=1)


@dataclass(frozen=True, slots=True)
class GeometryJob:
    """One unit of lane work: either the current request or one speculative prefetch."""

    worker: SubtitleGeometryWorker
    current: tuple[GeometryReservation, GeometryRequestBuilder, str | None] | None
    prefetch: tuple[str, tuple[int, GeometryRequestBuilder]] | None


class _Prefetched(NamedTuple):
    """One speculative result, with the render identity it was filed under.

    The key is carried rather than recomputed: this map is keyed by the caller's cue key while the
    result cache is keyed by `cache_key()`, and reconciling them is per-cue work on the coverage
    budget's path. `cache_key()` hashes the whole document and every embedded font, so deriving it
    there costs milliseconds under the worker lock on an attachment-heavy release.
    """

    cache_key: str
    request: GeometryRequest
    snapshot: GeometrySnapshot


def run_geometry_job(request: object, cancelled: threading.Event) -> object:
    _ = cancelled  # the coordinator's generation fence already retires superseded work
    if not isinstance(request, GeometryJob):
        raise TypeError("invalid subtitle-geometry request")
    request.worker.execute(request)
    return None


def configure_runtime_job(ipc) -> JobSubmitter | None:
    """Resolve the lane once, at composition. Absence becomes an explicit value here rather than a
    probe at each use site — see the sanctioned half of the capability-probe split."""
    return configure_lane(ipc, GEOMETRY_LANE, _GEOMETRY_LANE_POLICY, run_geometry_job)


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
        self._prefetched: OrderedDict[str, _Prefetched] = OrderedDict()
        self._prefetch_inflight_key: str | None = None
        self._prefetch_waiters: dict[str, GeometryReservation] = {}
        self._provenance: OrderedDict[str, GeometryCacheReason] = OrderedDict()
        self._history_lossy = False
        self._epoch_cause: GeometryCacheReason | None = None
        self._inflight = False
        self._pending_settled: Callable[[], None] | None = None
        #: Settlements owed by the job now executing — a current request's own, plus any caller
        #: that attached to an in-flight speculation instead of submitting its own job.
        self._settling: list[Callable[[], None]] = []
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
        self._coverage_trimmed = 0
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
        on_settled: Callable[[], None] | None = None,
    ) -> bool:
        """Queue the current request. ``on_settled`` runs when its lane terminal is delivered —
        that completion, not a tick, is what tells the host a result is ready to publish."""
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
                # No job of our own: the speculation already executing is what will answer, so its
                # terminal owes this caller the settlement.
                if on_settled is not None:
                    self._settling.append(on_settled)
                self._record_submit_latency(started)
                return True
            if work_key is not None and work_key in self._prefetch_pending:
                # Promote the queued speculation instead of running its builder: the current
                # request already carries the real one.
                self._prefetch_pending.pop(work_key)
            if self._pending is not None:
                self._superseded += 1
            self._pending = (reservation, build, work_key)
            self._pending_settled = on_settled
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
                self._remember_provenance(dropped, GeometryCacheReason.PREFETCH_SUPERSEDED)
        self._pump()
        return True

    def _pump(self) -> None:
        """Hand the next queued item to the lane, one in flight at a time.

        The queue policy stays here — a single current slot that supersedes, a bounded prefetch
        queue that drops oldest — because the broker owns admission and never feature state.

        Loops rather than recursing on a refusal: each round consumes one queued item, so the depth
        would be the prefetch queue's length, and that is `cache_max` — a config value with no upper
        bound. A gateway at capacity and a generous `cache_max` is a `RecursionError` on the terminal
        thread; iterating removes the class rather than bounding it.
        """
        while True:
            claimed = self._claim_next()
            if claimed is None:
                return
            job, identity = claimed
            if self._submit(
                owner=Owner.SUBTITLE,
                identity=identity,
                lane=GEOMETRY_LANE,
                request=job,
                on_finished=self._delivered,
            ):
                return
            self._unwind_refusal(job)

    def _claim_next(self) -> tuple[GeometryJob, tuple[str, int]] | None:
        """Take the next item and mark the lane busy, or `None` when there is nothing to hand over."""
        with self._condition:
            if self._closed or self._inflight:
                return None
            pending, self._pending = self._pending, None
            if pending is not None:
                job = GeometryJob(self, pending, None)
                if self._pending_settled is not None:
                    self._settling.append(self._pending_settled)
                self._pending_settled = None
            elif self._prefetch_pending:
                entry = self._prefetch_pending.popitem(last=False)
                self._prefetch_inflight_key = entry[0]
                job = GeometryJob(self, None, entry)
            else:
                return None
            self._inflight = True
            self._issued += 1
            return job, (GEOMETRY_LANE, self._issued)

    def _unwind_refusal(self, job: GeometryJob) -> None:
        """Give back what a refused submit claimed, and pay what its terminal now never will.

        Refused admission is dropped work, not queued work: left marked in flight it stalls every
        later request behind it. Left queued, its settlements are paid by the NEXT terminal, running
        one cue's callback against another cue's snapshot with its owner nowhere in the traceback.

        The drain takes all of `_settling`, not this job's share, and that is safe for a narrower
        reason than "they are all ours": `_idle` clears `_inflight` before its terminal fires, so a
        pump can claim and be refused while a previous job's callbacks are still queued. Every
        `_idle` site sits downstream of the publish those callbacks report, so paying them here is
        early rather than wrong. Move `_idle` above a publish and that stops being true.
        """
        with self._condition:
            self._inflight = False
            if job.current is not None:
                self._superseded += 1
            else:
                # A caller can attach to an in-flight speculation between its key being published
                # and the submit returning, registering a waiter and no job of its own.
                key = self._prefetch_inflight_key or ""
                if self._prefetch_waiters.pop(key, None) is not None:
                    self._superseded += 1
                self._remember_provenance(key, GeometryCacheReason.PREFETCH_SUPERSEDED)
                self._prefetch_inflight_key = None
                self._prefetch_dropped += 1
            owed, self._settling = self._settling, []
            self._condition.notify_all()
        for settle in owed:
            settle()

    def execute(self, job: GeometryJob) -> None:
        """Run one lane job. Called on a lane worker thread by :func:`run_geometry_job`."""
        if job.current is not None:
            self._process_current(job.current)
        else:
            assert job.prefetch is not None
            self._process_prefetch(job.prefetch)

    def _delivered(self, _completion: EffectFinished) -> None:
        """The lane terminal. Admission of the next job happens here rather than at the end of the
        handler, so work enters from the thread that consumes terminals.

        It also pays out the settlements this job owed, which is how a result reaches the host.
        """
        with self._condition:
            settling, self._settling = self._settling, []
        for settle in settling:
            settle()
        self._pump()

    def _remember_provenance(self, key: str, reason: GeometryCacheReason) -> None:
        self._provenance.pop(key, None)
        self._provenance[key] = reason
        while len(self._provenance) > self._cache_max:
            self._provenance.popitem(last=False)
            self._history_lossy = True

    def prefetch_miss_reason(self, key: str) -> GeometryCacheReason:
        with self._condition:
            if key in self._prefetch_pending or key == self._prefetch_inflight_key:
                return GeometryCacheReason.PREFETCH_PENDING
            recent = self._provenance.get(key)
            if recent is not None:
                return recent
            if self._epoch_cause is not None:
                cause, self._epoch_cause = self._epoch_cause, None
                return cause
            return (
                GeometryCacheReason.PROVENANCE_UNKNOWN
                if self._history_lossy
                else GeometryCacheReason.FIRST_SEEN
            )

    def publish_prefetched(self, key: str, generation: int) -> GeometryRequest | None:
        reservation = self._coordinator.reserve(generation)
        if reservation is None:
            return None
        with self._condition:
            cached = self._prefetched.pop(key, None)
            if cached is None:
                return None
            self._prefetched[key] = cached
        request, result = cached.request, cached.snapshot
        rebound_request = dataclass_replace(request, generation=generation)
        rebound_result = dataclass_replace(result, generation=generation)
        ticket = self._coordinator.bind(reservation, rebound_request)
        if ticket is None or not self._coordinator.publish(ticket, rebound_result):
            return None
        with self._condition:
            self._cache_hits += 1
            self._completed += 1
        return rebound_request

    def invalidate_cache(self, *, cause: GeometryCacheReason | None = None) -> None:
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

    def invalidate(self, *, cause: GeometryCacheReason | None = None) -> int:
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

    def _store(
        self, request: GeometryRequest, result: GeometrySnapshot, cache_key: str | None = None
    ) -> None:
        # The key is accepted rather than always derived: hashing the document and its fonts twice
        # for one store is the caller's saving to give, and the prefetch path already holds it.
        key = cache_key if cache_key is not None else request.cache_key()
        with self._condition:
            self._cache.pop(key, None)
            self._cache[key] = result
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)
            self._trim_coverage()

    @property
    def retained_coverage_bytes(self) -> int:
        with self._condition:
            return sum(snapshot.coverage_bytes for _key, snapshot in self._retained())

    def _retained(self) -> list[tuple[str, GeometrySnapshot]]:
        """Every snapshot this worker holds, oldest first, once each, by render identity.

        Both maps, because the same snapshot sits in the result cache and the prefetch map and
        counting one of them halves the answer. They are keyed differently — the cache by
        `cache_key()`, the prefetch map by the caller's cue key — so render identity is the only
        thing that can say whether two entries are one snapshot; comparing the raw keys makes every
        entry look unique, which double-counts the total and hides the cache's copy from `_strip`.

        The prefetch-only ones come first: every prefetch is written to both maps, so a render the
        cache no longer holds is one the cache evicted, which makes it the oldest thing here.
        """
        prefetch_only = [
            (entry.cache_key, entry.snapshot)
            for entry in self._prefetched.values()
            if entry.cache_key not in self._cache
        ]
        return prefetch_only + list(self._cache.items())

    def _trim_coverage(self) -> None:
        """Hold the retained coverage under its byte budget, oldest cue first.

        The entry bound is a count, and coverage is the one part of a snapshot whose size a count
        does not bound: a lookahead window of full-screen signs is a window of megabyte alpha masks.

        What is dropped is the masks, not the entries. A snapshot without coverage is still a
        complete set of hit boxes; its tokens fall from the raster colour device to the rule device,
        which needs nothing. So the budget costs a plainer mark, where evicting the entry would cost
        a re-render of a cue that is about to be shown.
        """
        retained = self._retained()
        total = sum(snapshot.coverage_bytes for _key, snapshot in retained)
        for key, snapshot in retained:
            if total <= COVERAGE_BUDGET_BYTES:
                return
            if not snapshot.coverage_bytes:
                continue
            total -= snapshot.coverage_bytes
            self._strip(key, snapshot.without_coverage())
            self._coverage_trimmed += 1

    def _strip(self, key: str, stripped: GeometrySnapshot) -> None:
        """Replace one snapshot in every map that holds it, addressed by render identity.

        Both maps, or the copy the other keeps makes the trim free nothing. The prefetch map is
        keyed by the caller's cue key, so its entry is found through its request, not through `key`.
        """
        if key in self._cache:
            self._cache[key] = stripped  # assignment to an existing key keeps its LRU position
        for cue, entry in list(self._prefetched.items()):
            if entry.cache_key == key:
                self._prefetched[cue] = entry._replace(snapshot=stripped)

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
            cache_key = request.cache_key()
            self._prefetched.pop(key, None)
            self._prefetched[key] = _Prefetched(cache_key, request, result)
            self._store(request, result, cache_key)
            self._prefetched_count += 1
            while len(self._prefetched) > self._cache_max:
                evicted, _cached = self._prefetched.popitem(last=False)
                self._remember_provenance(evicted, GeometryCacheReason.EVICTED)
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
                self._coverage_trimmed,
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
            # Same door as a refusal: no terminal is coming, so an unpaid settlement is one its
            # caller waits on forever. Paying it after close is what makes the wait bounded.
            owed, self._settling = self._settling, []
            self._pending_settled = None
            self._condition.notify_all()
        for settle in owed:
            settle()
        if self._local is not None:
            self._local.close()
        self._coordinator.close()
