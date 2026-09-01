from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest
from saitenka_subtitles import (
    GeometryRequest,
    GeometrySnapshot,
    GeometryVariant,
    Rect,
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
    TokenGeometry,
)
from session_builder import build_session

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_geometry_job import SubtitleGeometryWorker
from saitenka.app.subtitle_pipeline import (
    GeometryResolution,
    GeometryTicket,
    SubtitleModeCoordinator,
)
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


class FakeCurrentRenderer(NullRenderer):
    """Inherits the inert renderer so it answers the whole protocol; records what crosses the seam."""

    def __init__(self) -> None:
        self.drawn: object | None = None
        self.closed = False

    def draw(self, request: object, _surfaces=None, _ipc=None, /, **_ports) -> None:
        self.drawn = request

    def close(self) -> None:
        self.closed = True


class FakeGeometryBackend:
    def __init__(self) -> None:
        self.closed = False

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        if self.closed:
            raise RuntimeError("render after close")
        event_id = request.frame_id.active_event_ids[0]
        token = TokenGeometry(event_id, 0, Rect(10, 20, 30, 40))
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.frame_id,
            request.timestamp_ms,
            request.variant,
            (token,),
        )

    def close(self) -> None:
        if self.closed:
            raise RuntimeError("closed twice")
        self.closed = True


class WrongEventBackend(FakeGeometryBackend):
    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        result = super().render(request)
        return GeometrySnapshot(
            result.generation,
            result.track_id,
            SubtitleFrameId(
                result.track_id,
                (SubtitleEventId(result.track_id, 2_000, 3_000, 0, 3),),
            ),
            result.timestamp_ms,
            result.variant,
            result.tokens,
        )


class WrongVariantBackend(FakeGeometryBackend):
    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        result = super().render(request)
        return GeometrySnapshot(
            result.generation,
            result.track_id,
            result.frame_id,
            result.timestamp_ms,
            GeometryVariant.NATIVE,
            result.tokens,
        )


class FailingBackend(FakeGeometryBackend):
    def render(self, _request: GeometryRequest) -> GeometrySnapshot:
        raise RuntimeError("font provider unavailable")


class BlockingBackend(FakeGeometryBackend):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        self.entered.set()
        assert self.release.wait(1)
        return super().render(request)


class BlockingFailingBackend(BlockingBackend):
    def render(self, _request: GeometryRequest) -> GeometrySnapshot:
        self.entered.set()
        assert self.release.wait(1)
        raise RuntimeError("font provider unavailable")


class ConsumingCoordinator(SubtitleModeCoordinator):
    def resolve_outcome(self, ticket: GeometryTicket) -> GeometryResolution:
        outcome = super().resolve_outcome(ticket)
        self.consume_error()
        return outcome


def request(generation: int, timestamp_ms: int = 1_250) -> GeometryRequest:
    track_id = SubtitleTrackId("track-1")
    event_id = SubtitleEventId(track_id, 1_000, 2_000, 0, 2)
    return GeometryRequest(
        generation=generation,
        track_id=track_id,
        frame_id=SubtitleFrameId(track_id, (event_id,)),
        timestamp_ms=timestamp_ms,
        frame_size=(1920, 1080),
        storage_size=(1920, 1080),
        ass=b"[Script Info]\n",
    )


def test_coordinator_publishes_matching_generation() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())

    result = coordinator.render(request(coordinator.generation))

    assert result == coordinator.current
    assert result is not None
    assert result.tokens[0].bounds.contains(25, 30)


def test_coordinator_rejects_obsolete_generation() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    obsolete = request(coordinator.generation)

    coordinator.invalidate()

    assert coordinator.render(obsolete) is None
    assert coordinator.current is None


def test_coordinator_rejects_mismatched_backend_result() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), WrongEventBackend())

    assert coordinator.render(request(coordinator.generation)) is None
    assert coordinator.current is None


def test_coordinator_rejects_mismatched_variant() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), WrongVariantBackend())

    assert coordinator.render(request(coordinator.generation)) is None
    assert coordinator.current is None


def test_coordinator_rejects_superseded_same_generation_result() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    older = request(coordinator.generation, 1_250)
    newer = request(coordinator.generation, 1_300)
    older_ticket = coordinator.prepare(older)
    newer_ticket = coordinator.prepare(newer)
    assert older_ticket is not None and newer_ticket is not None
    older_result = FakeGeometryBackend().render(older)
    newer_result = FakeGeometryBackend().render(newer)

    assert not coordinator.publish(older_ticket, older_result)
    assert coordinator.publish(newer_ticket, newer_result)
    assert coordinator.current == newer_result


def test_coordinator_contains_optional_provider_failure() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FailingBackend())

    assert coordinator.render(request(coordinator.generation)) is None
    assert coordinator.current is None
    assert coordinator.last_error == "font provider unavailable"


def test_coordinator_close_invalidates_and_is_idempotent() -> None:
    backend = FakeGeometryBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    generation = coordinator.generation
    assert coordinator.render(request(generation)) is not None

    coordinator.close()
    coordinator.close()

    assert coordinator.current is None
    assert coordinator.render(request(generation)) is None
    assert backend.closed


def test_geometry_values_are_immutable() -> None:
    rect = Rect(1, 2, 3, 4)

    with pytest.raises(FrozenInstanceError):
        rect.x = 5  # type: ignore[misc]


def test_coordinator_delegates_current_renderer() -> None:
    """The coordinator hands the renderer a request, not the host it built it from.

    `object()` no longer stands in for a reader: the point of the seam is that the renderer never
    sees one, so the double asserts on what crosses it instead.
    """
    from util import FakeIPC

    renderer = FakeCurrentRenderer()
    reader = build_session(
        FakeIPC(),
        infrastructure=SessionInfrastructure(
            renderer=renderer,
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    coordinator = reader.graph.subtitle_presentation.pipeline
    coordinator.renderer = renderer

    coordinator.draw_current(reader.graph.subtitle_presentation.target())

    assert renderer.drawn is not None
    assert renderer.drawn is not reader
    assert renderer.drawn.osd == reader.graph.screen.osd
    reader.close()


def test_worker_drops_superseded_pending_request() -> None:
    """Only one current request waits at a time: a newer one replaces the queued older one rather
    than queueing behind it, so the older never reaches the lane at all."""
    backend = BlockingBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    first = request(coordinator.generation, 1_250)
    second = request(coordinator.generation, 1_300)

    # Occupy the lane so both currents queue rather than executing on arrival.
    assert worker.prefetch("blocking", coordinator.generation, lambda: request(0, 1_100))
    assert backend.entered.wait(1)
    assert worker.submit(first)
    assert worker.submit(second)
    backend.release.set()
    assert worker.wait_idle()

    assert coordinator.current is not None
    assert coordinator.current.timestamp_ms == 1_300
    assert worker.stats.submitted == 2
    assert worker.stats.completed == 1
    assert worker.stats.superseded == 1
    worker.close()


def test_worker_cache_rebinds_generation_after_invalidation() -> None:
    backend = FakeGeometryBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator)
    first = request(coordinator.generation)
    assert worker.submit(first) and worker.wait_idle()
    coordinator.invalidate()
    repeated = request(coordinator.generation)

    assert worker.submit(repeated) and worker.wait_idle()

    assert coordinator.current is not None
    assert coordinator.current.generation == repeated.generation
    assert worker.stats.cache_hits == 1
    worker.close()


def test_worker_readiness_instrument_rejects_stale_geometry() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator)
    current = request(coordinator.generation)
    assert not worker.mark_presented(current)
    assert worker.submit(current) and worker.wait_idle()
    assert worker.mark_presented(current)
    coordinator.invalidate()
    stale = request(coordinator.generation)
    assert not worker.mark_presented(stale)

    assert worker.stats.ready_before_presented == 1
    assert worker.stats.presented == 3
    worker.close()


def test_worker_contains_provider_failure_and_stops() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FailingBackend())
    worker = SubtitleGeometryWorker(coordinator)

    assert worker.submit(request(coordinator.generation))
    assert worker.wait_idle()
    assert worker.stats.failures == 1
    assert coordinator.last_error == "font provider unavailable"

    worker.close()


def test_worker_drops_provider_failure_invalidated_during_render() -> None:
    backend = BlockingFailingBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator)

    assert worker.submit(request(coordinator.generation))
    assert backend.entered.wait(1)
    worker.invalidate()
    backend.release.set()
    assert worker.wait_idle()

    assert coordinator.last_error is None
    assert worker.stats.failures == 0
    assert worker.stats.superseded == 1
    worker.close()


def test_worker_counts_failure_after_error_is_consumed() -> None:
    coordinator = ConsumingCoordinator(FakeCurrentRenderer(), FailingBackend())
    worker = SubtitleGeometryWorker(coordinator)

    assert worker.submit(request(coordinator.generation))
    assert worker.wait_idle()

    assert coordinator.last_error is None
    assert worker.stats.failures == 1
    worker.close()


def test_worker_builds_request_off_submit_thread() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator)
    caller = threading.get_ident()
    built_on: list[int] = []

    def build() -> GeometryRequest:
        built_on.append(threading.get_ident())
        return request(coordinator.generation)

    assert worker.submit_job(coordinator.generation, build)
    assert worker.wait_idle()

    assert built_on and built_on[0] != caller
    assert coordinator.current is not None
    worker.close()


def test_worker_drops_request_built_after_newer_reservation() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator)
    entered = threading.Event()
    release = threading.Event()

    def slow() -> GeometryRequest:
        entered.set()
        assert release.wait(1)
        return request(coordinator.generation, 1_250)

    assert worker.submit_job(coordinator.generation, slow)
    assert entered.wait(1)
    assert worker.submit(request(coordinator.generation, 1_300))
    release.set()
    assert worker.wait_idle()

    assert coordinator.current is not None
    assert coordinator.current.timestamp_ms == 1_300
    assert worker.stats.superseded == 1
    worker.close()


def test_worker_records_source_preparation_failure() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator)

    def fail() -> GeometryRequest:
        raise ValueError("authored event unavailable")

    assert worker.submit_job(coordinator.generation, fail)
    assert worker.wait_idle()

    assert coordinator.current is None
    assert coordinator.last_error == "authored event unavailable"
    assert worker.stats.failures == 1
    worker.close()


def test_worker_prefetch_publishes_synchronously_after_generation_change() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    future = request(coordinator.generation, 1_300)

    assert worker.prefetch("future", coordinator.generation, lambda: future)
    assert worker.wait_idle()
    assert coordinator.current is None
    coordinator.invalidate()

    published = worker.publish_prefetched("future", coordinator.generation)

    assert published is not None
    assert published.generation == coordinator.generation
    assert coordinator.current is not None
    assert coordinator.current.timestamp_ms == 1_300
    assert worker.stats.prefetched == 1
    worker.close()


def test_worker_drops_prefetch_invalidated_during_backend_render() -> None:
    backend = BlockingBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    future = request(coordinator.generation, 1_300)

    assert worker.prefetch("future", coordinator.generation, lambda: future)
    assert backend.entered.wait(1)
    worker.invalidate()
    backend.release.set()
    assert worker.wait_idle()

    assert worker.publish_prefetched("future", coordinator.generation) is None
    assert coordinator.current is None
    assert worker.stats.prefetch_dropped == 1
    worker.close()


def test_worker_attaches_current_waiter_to_inflight_prefetch() -> None:
    backend = BlockingBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    future = request(coordinator.generation, 1_300)

    assert worker.prefetch("future", coordinator.generation, lambda: future)
    assert backend.entered.wait(1)
    assert worker.submit_job(
        coordinator.generation,
        lambda: future,
        work_key="future",
    )
    backend.release.set()
    assert worker.wait_idle()

    assert coordinator.current is not None
    assert coordinator.current.timestamp_ms == 1_300
    assert worker.stats.completed == 1
    assert worker.stats.prefetched == 1
    worker.close()


def test_worker_promotes_queued_prefetch_without_running_its_builder() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    entered = threading.Event()
    release = threading.Event()
    prefetch_builds = 0

    def blocked_current() -> GeometryRequest:
        entered.set()
        assert release.wait(1)
        return request(coordinator.generation, 1_200)

    def speculative() -> GeometryRequest:
        nonlocal prefetch_builds
        prefetch_builds += 1
        return request(coordinator.generation, 1_300)

    assert worker.submit_job(coordinator.generation, blocked_current)
    assert entered.wait(1)
    assert worker.prefetch("future", coordinator.generation, speculative)
    assert worker.submit_job(
        coordinator.generation,
        lambda: request(coordinator.generation, 1_300),
        work_key="future",
    )
    release.set()
    assert worker.wait_idle()

    assert prefetch_builds == 0
    assert coordinator.current is not None
    assert coordinator.current.timestamp_ms == 1_300
    worker.close()


def test_worker_reports_failure_from_inflight_prefetch_with_current_waiter() -> None:
    backend = BlockingFailingBackend()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), backend)
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    future = request(coordinator.generation, 1_300)

    assert worker.prefetch("future", coordinator.generation, lambda: future)
    assert backend.entered.wait(1)
    assert worker.submit_job(
        coordinator.generation,
        lambda: future,
        work_key="future",
    )
    backend.release.set()
    assert worker.wait_idle()

    assert coordinator.current is None
    assert coordinator.last_error == "font provider unavailable"
    assert worker.stats.failures == 1
    worker.close()


def test_worker_bounds_prefetch_queue_and_records_drop() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=1)
    entered = threading.Event()
    release = threading.Event()

    def blocking() -> GeometryRequest:
        entered.set()
        assert release.wait(1)
        return request(coordinator.generation, 1_200)

    assert worker.prefetch("running", coordinator.generation, blocking)
    assert entered.wait(1)
    assert worker.prefetch("drop", coordinator.generation, lambda: request(0, 1_300))
    assert worker.prefetch("keep", coordinator.generation, lambda: request(0, 1_400))
    release.set()
    assert worker.wait_idle()

    assert worker.stats.prefetch_dropped == 1
    worker.close()


def test_worker_reports_loss_aware_prefetch_miss_provenance() -> None:
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=1)
    entered = threading.Event()
    release = threading.Event()

    def blocking() -> GeometryRequest:
        entered.set()
        assert release.wait(1)
        return request(coordinator.generation, 1_200)

    assert worker.submit_job(coordinator.generation, blocking)
    assert entered.wait(1)
    assert worker.prefetch("oldest", coordinator.generation, lambda: request(0, 1_300))
    assert worker.prefetch("middle", coordinator.generation, lambda: request(0, 1_400))
    assert worker.prefetch("newest", coordinator.generation, lambda: request(0, 1_500))

    assert worker.prefetch_miss_reason("newest") == "prefetch-pending"
    assert worker.prefetch_miss_reason("oldest") == "provenance-unknown"
    assert worker.prefetch_miss_reason("never-seen") == "provenance-unknown"
    release.set()
    assert worker.wait_idle()
    worker.close()


def test_a_gatewayed_session_runs_geometry_on_the_broker_lane(request) -> None:
    """The composition seam, pinned. `configure_runtime_job` resolves the lane once here; if it
    silently returned None the worker would fall back to its local lane and production geometry
    would never reach the broker — bounded admission and close would both be someone else's.
    """
    from util import FakeIPC, bare_gateway

    from saitenka.app.subtitle_geometry_job import GEOMETRY_LANE, configure_runtime_job

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    ipc.install_runtime_ingress(lambda *_a: None, lambda *_a: None, None, gateway)

    submit = configure_runtime_job(ipc)

    assert submit is not None
    assert ipc.close_runtime_job_lane(GEOMETRY_LANE, 1.0)  # registered, and closeable by name


def test_an_ungatewayed_session_still_executes_geometry() -> None:
    """Negative control for the lane resolution: without a broker the worker owns a local lane, so
    there is one execution path rather than a silently disabled feature."""
    from util import FakeIPC

    from saitenka.app.subtitle_geometry_job import configure_runtime_job

    assert configure_runtime_job(FakeIPC()) is None

    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, submit=None)

    assert worker.submit(request(coordinator.generation)) and worker.wait_idle()

    assert coordinator.current is not None
    worker.close()


class SaturatedLane:
    """A lane that refuses admission while ``full``, and otherwise runs the job inline.

    Refusal is what the broker answers at capacity, and it is the one arm no other test reaches —
    every fake so far admits everything. The worker has to read it as dropped work rather than as
    work in flight, or one refusal wedges the queue for the rest of the session.
    """

    def __init__(self) -> None:
        self.full = True
        self.admitted = 0

    def __call__(self, *, owner, identity, lane, request, on_finished) -> bool:  # noqa: ARG002
        if self.full:
            return False
        self.admitted += 1
        request.worker.execute(request)
        on_finished(EffectFinished(EffectId(0), owner, identity, EffectOutcome.SUCCEEDED))
        return True


class DeferredLane(SaturatedLane):
    """A lane that admits the job and holds it, so a test picks what lands before it executes."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list[tuple[object, object, object]] = []

    def __call__(self, *, owner, identity, lane, request, on_finished) -> bool:  # noqa: ARG002
        self.held.append((request, identity, on_finished))
        return True

    def run_one(self, owner) -> None:
        job, identity, on_finished = self.held.pop(0)
        job.worker.execute(job)
        on_finished(EffectFinished(EffectId(0), owner, identity, EffectOutcome.SUCCEEDED))


def test_a_refused_lane_admission_drops_the_work_without_wedging_the_queue() -> None:
    lane = SaturatedLane()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, submit=lane)

    assert worker.submit(request(coordinator.generation))  # reserved, then refused admission

    assert lane.admitted == 0
    assert coordinator.current is None
    assert worker.stats.superseded == 1  # counted as dropped, not left pending

    lane.full = False
    assert worker.submit(request(coordinator.generation))

    assert lane.admitted == 1
    assert coordinator.current is not None  # nothing believed a job was still in flight
    worker.close()


def test_a_job_that_executes_after_close_publishes_nothing_and_takes_no_successor() -> None:
    """Close quarantine, driven at the lane rather than at the coordinator.

    A job already admitted cannot be recalled, so the contract is about what its result is allowed
    to do on arrival — a session that has torn down its surface must not have pixels handed to it
    by work it started before.
    """
    lane = DeferredLane()
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, submit=lane)
    assert worker.submit(request(coordinator.generation))
    assert lane.held  # admitted, not yet executed

    worker.close()
    lane.run_one(Owner.SUBTITLE)

    assert coordinator.current is None
    assert not worker.submit(request(coordinator.generation))
    assert not lane.held  # and the terminal admitted no successor behind it


def test_new_epoch_reports_its_invalidation_cause_once() -> None:
    worker = SubtitleGeometryWorker(
        SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend()), cache_max=2
    )
    worker.invalidate(cause="source-changed")

    assert worker.prefetch_miss_reason("first-new-key") == "source-changed"
    assert worker.prefetch_miss_reason("second-new-key") == "first-seen"

    worker.close()


class HeavyCoverageBackend(FakeGeometryBackend):
    """A cue whose coverage masks are a megabyte — a full-screen sign, not a pathological input."""

    MASK = b"\xff" * (1024 * 1024)

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        result = super().render(request)
        return GeometrySnapshot(
            result.generation,
            result.track_id,
            result.frame_id,
            result.timestamp_ms,
            result.variant,
            tuple(
                TokenGeometry(
                    token.event_id, token.token_index, token.bounds, (), "", 0.0, self.MASK
                )
                for token in result.tokens
            ),
        )


def fill_cache(worker: SubtitleGeometryWorker, coordinator, count: int) -> None:
    for index in range(count):
        assert worker.prefetch(
            f"cue-{index}",
            coordinator.generation,
            lambda index=index: request(coordinator.generation, 1_300 + index),
        )
        assert worker.wait_idle()


def test_retained_coverage_is_bounded_by_bytes_not_by_entry_count(monkeypatch) -> None:
    """The entry bound does not bound this. Coverage is the one part of a snapshot whose size a
    count says nothing about, and lookahead holds a window of cues, so a run of full-screen signs
    grows the retained alpha without any meter moving."""
    from saitenka.app import subtitle_geometry_job

    monkeypatch.setattr(subtitle_geometry_job, "COVERAGE_BUDGET_BYTES", 2 * 1024 * 1024)
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), HeavyCoverageBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=8)

    fill_cache(worker, coordinator, 5)

    assert worker.retained_coverage_bytes <= 2 * 1024 * 1024
    assert worker.stats.coverage_trimmed > 0
    worker.close()


def test_the_budget_drops_the_masks_and_keeps_the_boxes(monkeypatch) -> None:
    """What the budget evicts is the color device's input, not the cue. A stripped snapshot is
    still a complete set of hit boxes — its tokens fall to the rule device, which needs nothing —
    where evicting the entry would cost a re-render of a cue about to be shown."""
    from saitenka.app import subtitle_geometry_job

    monkeypatch.setattr(subtitle_geometry_job, "COVERAGE_BUDGET_BYTES", 1)
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), HeavyCoverageBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=4)

    fill_cache(worker, coordinator, 2)
    assert worker.publish_prefetched("cue-1", coordinator.generation) is not None

    published = coordinator.current
    assert published is not None
    assert published.tokens[0].bounds.contains(25, 30), "the hit boxes did not survive the trim"
    assert published.tokens[0].coverage == b""
    worker.close()


def test_the_oldest_cue_gives_up_its_masks_first(monkeypatch) -> None:
    """The cue about to be drawn is the one that still needs its raster; a speculative one measured
    three cues ahead is the cheapest color to lose."""
    from saitenka.app import subtitle_geometry_job

    monkeypatch.setattr(subtitle_geometry_job, "COVERAGE_BUDGET_BYTES", 1024 * 1024)
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), HeavyCoverageBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=4)

    fill_cache(worker, coordinator, 2)

    assert worker.publish_prefetched("cue-0", coordinator.generation) is not None
    first = coordinator.current
    coordinator.invalidate()
    assert worker.publish_prefetched("cue-1", coordinator.generation) is not None
    second = coordinator.current

    assert first is not None and second is not None
    assert first.tokens[0].coverage == b""
    assert second.tokens[0].coverage == HeavyCoverageBackend.MASK
    worker.close()


def test_the_trim_treats_a_cue_the_result_cache_evicted_as_the_oldest(monkeypatch) -> None:
    """ "Oldest first" spans two maps, and they do not agree on age by construction.

    Every prefetch is written to both, but current requests fill only the result cache — so a key
    the result cache no longer holds is one it evicted, which makes it the oldest thing the worker
    has. Listing the cache first put it last, and the trim then took the masks off the cue nearest
    to being drawn while keeping them for one already gone.
    """
    from saitenka.app import subtitle_geometry_job

    mask = len(HeavyCoverageBackend.MASK)
    monkeypatch.setattr(subtitle_geometry_job, "COVERAGE_BUDGET_BYTES", 2 * mask)
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), HeavyCoverageBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)

    fill_cache(worker, coordinator, 2)
    # A current request of its own: this is what pushes cue-0 out of the result cache while the
    # prefetch map keeps it, which is the only way the two orderings come apart.
    assert worker.submit_job(coordinator.generation, lambda: request(coordinator.generation, 9_999))
    assert worker.wait_idle()

    # The newer cue first: publishing re-enters the cache and can trim again, so reading the one
    # under test last would be reading the state of a later trim.
    assert worker.publish_prefetched("cue-1", coordinator.generation) is not None
    newer = coordinator.current
    coordinator.invalidate()
    assert worker.publish_prefetched("cue-0", coordinator.generation) is not None
    evicted = coordinator.current

    assert evicted is not None and newer is not None
    assert evicted.tokens[0].coverage == b"", "the oldest cue kept its masks"
    assert newer.tokens[0].coverage == HeavyCoverageBackend.MASK


def test_close_pays_the_settlements_no_terminal_will_ever_deliver() -> None:
    """Closing retires the lane, so a queued settlement has nothing left to arrive and pay it. Its
    caller is told the work finished — which is true, it finished by ending — rather than waiting on
    a completion the worker has already stopped being able to produce."""
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    settled: list[str] = []
    holding = threading.Event()
    worker = SubtitleGeometryWorker(
        coordinator, cache_max=4, submit=lambda **_kwargs: holding.is_set()
    )
    holding.set()  # admitted, so the job stays in flight and its settlement stays queued
    assert worker.submit_job(
        coordinator.generation,
        lambda: request(coordinator.generation),
        on_settled=lambda: settled.append("owed"),
    )
    assert settled == [], "the terminal never fired, so nothing should have settled yet"

    worker.close()

    assert settled == ["owed"]


def test_a_caller_that_attached_to_a_refused_speculation_is_settled_too() -> None:
    """The other way a caller is owed a settlement, and the one a refusal used to strand.

    `_pump` publishes the in-flight prefetch key inside the lock and submits outside it. A caller
    whose request matches that key in the window between registers a waiter and no job of its own,
    appending its callback directly. Paying only the queued one left this in `_settling` for the
    next terminal — one cue's callback against another cue's snapshot.

    The submitter drives the interleaving instead of a thread, so the window is a fact of the code
    rather than of the scheduler.
    """
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    settled: list[str] = []
    worker: SubtitleGeometryWorker | None = None

    def refuse_after_a_caller_attaches(**_kwargs) -> bool:
        assert worker is not None
        worker.submit_job(
            coordinator.generation,
            lambda: request(coordinator.generation),
            work_key="cue-0",
            on_settled=lambda: settled.append("attached"),
        )
        return False

    worker = SubtitleGeometryWorker(coordinator, cache_max=4, submit=refuse_after_a_caller_attaches)
    assert worker.prefetch("cue-0", coordinator.generation, lambda: request(coordinator.generation))

    assert settled == ["attached"]
    assert not worker._prefetch_waiters, "the refused speculation left a waiter behind"
    worker.close()


def test_a_refused_lane_admission_settles_its_own_caller_and_no_one_elses() -> None:
    """A settlement is queued against the terminal of the job that owes it. Refused admission means
    that terminal never arrives, so leaving it queued hands it to the NEXT job's terminal — which
    runs this cue's callback against a later cue's snapshot, and the callback's owner is nowhere in
    the traceback when the token indices then disagree.
    """
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), HeavyCoverageBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=4, submit=lambda **_kwargs: False)
    settled: list[str] = []

    for name in ("first", "second"):
        assert worker.submit_job(
            coordinator.generation,
            lambda: request(coordinator.generation, 1_300),
            on_settled=lambda name=name: settled.append(name),
        )

    assert settled == ["first", "second"]
    worker.close()


def test_the_render_space_is_part_of_the_cache_key() -> None:
    """Not a freshness nicety: a snapshot's coverage masks are rasterised at this frame's pixels and
    uploaded as a bitmap, which mpv does not rescale with its OSD surface the way it rescales a text
    payload. Drop the frame from the key and a resize paints the old window's colors over the new
    window's glyphs."""
    base = request(1)
    resized = GeometryRequest(
        generation=base.generation,
        track_id=base.track_id,
        frame_id=base.frame_id,
        timestamp_ms=base.timestamp_ms,
        frame_size=(1280, 720),
        storage_size=base.storage_size,
        ass=base.ass,
    )

    assert base.cache_key() != resized.cache_key()
