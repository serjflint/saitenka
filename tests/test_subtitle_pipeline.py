from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
from saitenka.subtitles import (
    GeometryRequest,
    GeometrySnapshot,
    GeometryVariant,
    Rect,
    SubtitleEventId,
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
        token = TokenGeometry(0, Rect(10, 20, 30, 40))
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.event_id,
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
            SubtitleEventId(result.track_id, 2_000, 3_000, 0, 3),
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
            result.event_id,
            result.timestamp_ms,
            GeometryVariant.NATIVE,
            result.tokens,
        )


class FailingBackend(FakeGeometryBackend):
    def render(self, _request: GeometryRequest) -> GeometrySnapshot:
        raise RuntimeError("font provider unavailable")


def request(generation: int, timestamp_ms: int = 1_250) -> GeometryRequest:
    track_id = SubtitleTrackId("track-1")
    return GeometryRequest(
        generation=generation,
        track_id=track_id,
        event_id=SubtitleEventId(track_id, 1_000, 2_000, 0, 2),
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
