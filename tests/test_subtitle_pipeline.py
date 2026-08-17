from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from saitenka.app.subtitle_pipeline import (
    GeometryResolution,
    GeometryTicket,
    SubtitleGeometryWorker,
    SubtitleModeCoordinator,
)
from saitenka.subtitles import (
    GeometryRequest,
    GeometrySnapshot,
    GeometryVariant,
    Rect,
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
    TokenGeometry,
)


class FakeCurrentRenderer:
    def __init__(self) -> None:
        self.drawn_host: object | None = None

    def draw(self, reader: object) -> None:
        self.drawn_host = reader


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
    renderer = FakeCurrentRenderer()
    coordinator = SubtitleModeCoordinator(renderer)
    host = object()

    coordinator.draw_current(host)  # type: ignore[arg-type]

    assert renderer.drawn_host is host


def test_worker_drops_superseded_pending_request(monkeypatch) -> None:
    start = threading.Event()
    run = SubtitleGeometryWorker._run

    def delayed_run(worker: SubtitleGeometryWorker) -> None:
        assert start.wait(1.0)
        run(worker)

    monkeypatch.setattr(SubtitleGeometryWorker, "_run", delayed_run)
    coordinator = SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend())
    worker = SubtitleGeometryWorker(coordinator, cache_max=2)
    first = request(coordinator.generation, 1_250)
    second = request(coordinator.generation, 1_300)

    assert worker.submit(first)
    assert worker.submit(second)
    start.set()
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


def test_new_epoch_reports_its_invalidation_cause_once() -> None:
    worker = SubtitleGeometryWorker(
        SubtitleModeCoordinator(FakeCurrentRenderer(), FakeGeometryBackend()), cache_max=2
    )
    worker.invalidate(cause="source-changed")

    assert worker.prefetch_miss_reason("first-new-key") == "source-changed"
    assert worker.prefetch_miss_reason("second-new-key") == "first-seen"

    worker.close()
