"""WP4.4: every geometry provider obeys one identity and bounded-failure contract.

libass, null, and the test fake are interchangeable capabilities. Anything asserted here must hold
for all of them, so swapping the provider can never change what the runtime is allowed to publish.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from saitenka.subtitles import (
    GeometryPaletteEntry,
    GeometryRequest,
    GeometrySnapshot,
    GeometryVariant,
    NullGeometryBackend,
    Rect,
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
    TokenGeometry,
)
from saitenka.subtitles.libass_backend import LibassGeometryBackend

TRACK = SubtitleTrackId("track")
EVENT = SubtitleEventId(TRACK, 1_000, 2_000, 0, 0)


def request(**overrides: object) -> GeometryRequest:
    base = GeometryRequest(
        0,
        TRACK,
        SubtitleFrameId(TRACK, (EVENT,)),
        1_250,
        (1280, 720),
        (1280, 720),
        b"ass",
        palette=(GeometryPaletteEntry(EVENT, 0, 0x010203),),
        reserved_rgb=(0xFFFFFF,),
        attachments=(("font.ttf", b"font"),),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


class FakeGeometryBackend:
    """The in-repo fake, held to the same contract as the shipping providers."""

    def __init__(self) -> None:
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        if self._closed:
            raise RuntimeError("fake geometry backend is closed")
        event = request.frame_id.active_event_ids[0]
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.frame_id,
            request.timestamp_ms,
            request.variant,
            (TokenGeometry(event, 0, Rect(10, 20, 30, 40)),),
        )

    def close(self) -> None:
        self._closed = True


@dataclass(frozen=True)
class _Layer:
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int
    image_type: int = 0


@dataclass(frozen=True)
class _Result:
    layers: tuple[_Layer, ...]


class _StubRenderer:
    """Stands in for libasslite: paints one character layer per requested palette colour."""

    def __init__(self, colors: list[int]) -> None:
        self._colors = colors
        self.closed = False

    def library_version(self) -> int:
        return 0x1600000

    def render(self, timestamp_ms: int, frame_size, storage_size, **_kwargs) -> _Result:
        self.last_render = (timestamp_ms, frame_size, storage_size)
        return _Result(
            tuple(
                _Layer(2, 1, b"\xff\xff", color << 8, 10 + index * 40, 20)
                for index, color in enumerate(self._colors)
            )
        )

    def close(self) -> None:
        self.closed = True


class StubbedLibassBackend:
    """The shipping libass backend, driven by a stub renderer so the conformance exercises its
    real identity, cache and close behaviour without the native package."""

    def __init__(self) -> None:
        self._colors: list[int] = []
        self._backend = LibassGeometryBackend(
            renderer_factory=lambda _ass, **_kwargs: _StubRenderer(self._colors)
        )

    @property
    def closed(self) -> bool:
        return self._backend.closed

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        self._colors[:] = [entry.rgb for entry in request.palette]
        return self._backend.render(request)

    def close(self) -> None:
        self._backend.close()


PROVIDERS = (
    pytest.param(StubbedLibassBackend, id="libass"),
    pytest.param(NullGeometryBackend, id="null"),
    pytest.param(FakeGeometryBackend, id="fake"),
)


# --- identity ----------------------------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_snapshot_echoes_the_requested_identity(provider) -> None:
    """A caller fences a result by comparing identity; a provider that rewrote it could smuggle
    a stale hit map past that check."""
    backend = provider()
    given = request(generation=7, timestamp_ms=4_200)

    snapshot = backend.render(given)

    assert snapshot.generation == given.generation
    assert snapshot.track_id == given.track_id
    assert snapshot.frame_id == given.frame_id
    assert snapshot.timestamp_ms == given.timestamp_ms
    assert snapshot.variant == given.variant


@pytest.mark.parametrize("provider", PROVIDERS)
def test_published_tokens_belong_to_the_requested_frame(provider) -> None:
    backend = provider()

    snapshot = backend.render(request())

    active = set(snapshot.frame_id.active_event_ids)
    assert all(token.event_id in active for token in snapshot.tokens)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "change",
    [
        {"timestamp_ms": 9_999},  # seek / sub-delay
        {"frame_size": (1920, 1080), "storage_size": (1920, 1080)},  # resize
        {"margins": (10, 10, 10, 10), "use_margins": True},
        {"pixel_aspect": 1.5},  # PAR
        {"generation": 3},
    ],
)
def test_a_changed_render_space_produces_a_correspondingly_identified_snapshot(
    provider, change: dict
) -> None:
    """Repeated identical text under a new render space must not reuse the old identity."""
    backend = provider()
    given = request(**change)

    snapshot = backend.render(given)

    assert snapshot.timestamp_ms == given.timestamp_ms
    assert snapshot.generation == given.generation


@pytest.mark.parametrize("provider", PROVIDERS)
def test_overlapping_authored_events_keep_their_own_identities(provider) -> None:
    second = SubtitleEventId(TRACK, 1_100, 2_400, 1, 1)
    given = request(
        frame_id=SubtitleFrameId(TRACK, (EVENT, second)),
        palette=(
            GeometryPaletteEntry(EVENT, 0, 0x010203),
            GeometryPaletteEntry(second, 0, 0x040506),
        ),
    )

    snapshot = provider().render(given)

    assert snapshot.frame_id.active_event_ids == (EVENT, second)


# --- bounded failure ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_rendering_after_close_raises_rather_than_publishing(provider) -> None:
    """A late render must fail loudly at the boundary, never return boxes for a torn-down
    session that the caller would then treat as current."""
    backend = provider()
    backend.close()

    with pytest.raises(RuntimeError):
        backend.render(request())


@pytest.mark.parametrize("provider", PROVIDERS)
def test_close_is_idempotent(provider) -> None:
    backend = provider()

    backend.close()
    backend.close()

    assert backend.closed


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_provider_never_mutates_the_request(provider) -> None:
    backend = provider()
    given = request()

    backend.render(given)

    assert given == request()


# --- the null provider is a real capability, not a failure -------------------------------------


def test_the_null_provider_degrades_interaction_without_failing() -> None:
    """No native renderer installed is an ordinary session: pixels show, hit boxes do not."""
    snapshot = NullGeometryBackend().render(request())

    assert snapshot.tokens == ()
    assert snapshot.variant is GeometryVariant.HIT_MAP
