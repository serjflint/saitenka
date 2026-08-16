from __future__ import annotations

from dataclasses import dataclass

import pytest

from saitenka.subtitles import (
    GeometryPaletteEntry,
    GeometryRequest,
    SubtitleEventId,
    SubtitleTrackId,
)
from saitenka.subtitles.libass_backend import LibassGeometryBackend, extract_token_geometry


@dataclass(frozen=True)
class Layer:
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int
    image_type: int = 0


@dataclass(frozen=True)
class Result:
    layers: tuple[Layer, ...]


def request(
    *,
    ass: bytes = b"ass",
    generation: int = 0,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    use_margins: bool = False,
    render_profile: tuple[tuple[str, str], ...] = (),
) -> GeometryRequest:
    track = SubtitleTrackId("track")
    event = SubtitleEventId(track, 1_000, 2_000, 0, 0)
    return GeometryRequest(
        generation,
        track,
        event,
        1_250,
        (1280, 720),
        (1280, 720),
        ass,
        margins=margins,
        use_margins=use_margins,
        palette=(
            GeometryPaletteEntry(event, 0, 0x010203),
            GeometryPaletteEntry(event, 1, 0x040506),
        ),
        reserved_rgb=(0xFFFFFF,),
        attachments=(("font.ttf", b"font"),),
        render_profile=render_profile,
    )


def test_extractor_unions_segments_and_ignores_non_character_layers() -> None:
    rendered = Result(
        (
            Layer(2, 1, b"\xff\x00", 0x01020300, 10, 20),
            Layer(2, 1, b"\x00\xff", 0x01020300, 12, 20),
            Layer(1, 1, b"\xff", 0x04050600, 30, 40),
            Layer(9, 9, b"\xff" * 81, 0x99999900, 0, 0, image_type=1),
        )
    )

    geometry = extract_token_geometry(rendered, request())

    assert [(item.token_index, item.bounds) for item in geometry] == [
        (0, type(geometry[0].bounds)(10, 20, 4, 1)),
        (1, type(geometry[1].bounds)(30, 40, 1, 1)),
    ]
    assert len(geometry[0].regions) == 2


@pytest.mark.parametrize(
    ("layers", "message"),
    [
        ((Layer(1, 1, b"\xff", 0x99999900, 0, 0),), "unknown"),
        ((Layer(1, 1, b"", 0x01020300, 0, 0),), "invalid size"),
        ((Layer(1, 1, b"\x00", 0x01020300, 0, 0),), "missing"),
    ],
)
def test_extractor_fails_closed_on_unusable_layers(layers, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extract_token_geometry(Result(layers), request())


def test_request_cache_identity_covers_render_inputs_but_not_generation() -> None:
    baseline = request()

    assert baseline.cache_key() == request(generation=9).cache_key()
    assert baseline.cache_key() != request(ass=b"changed").cache_key()
    assert baseline.cache_key() != request(margins=(10, 20, 30, 40)).cache_key()
    assert baseline.cache_key() != request(use_margins=True).cache_key()
    assert baseline.cache_key() != request(render_profile=(("sub-scale", "1.2"),)).cache_key()


@pytest.mark.parametrize(
    "margins",
    [(-1, 0, 0, 0), (360, 360, 0, 0), (0, 0, 640, 640)],
)
def test_request_rejects_invalid_frame_margins(margins: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="margins"):
        request(margins=margins)


def test_request_rejects_non_boolean_margin_policy() -> None:
    with pytest.raises(TypeError, match="use_margins"):
        request(use_margins=1)  # type: ignore[arg-type]


def test_request_rejects_cross_event_palette() -> None:
    baseline = request()
    other_track = SubtitleTrackId("other")
    other_event = SubtitleEventId(other_track, 1_000, 2_000, 0, 0)

    with pytest.raises(ValueError, match="requested event"):
        GeometryRequest(
            baseline.generation,
            baseline.track_id,
            baseline.event_id,
            baseline.timestamp_ms,
            baseline.frame_size,
            baseline.storage_size,
            baseline.ass,
            palette=(GeometryPaletteEntry(other_event, 0, 1),),
        )


class FakeRenderer:
    def __init__(self, result: Result) -> None:
        self.result = result
        self.closed = False
        self.calls: list[tuple[tuple, dict]] = []

    def render(self, *args, **kwargs) -> Result:
        self.calls.append((args, kwargs))
        return self.result

    def close(self) -> None:
        assert not self.closed
        self.closed = True


def test_backend_bounds_renderer_cache_and_closes_evictions() -> None:
    created: list[FakeRenderer] = []

    def factory(_ass, **_kwargs):
        renderer = FakeRenderer(
            Result(
                (
                    Layer(1, 1, b"\xff", 0x01020300, 10, 20),
                    Layer(1, 1, b"\xff", 0x04050600, 30, 40),
                )
            )
        )
        created.append(renderer)
        return renderer

    backend = LibassGeometryBackend(renderer_cache_max=1, renderer_factory=factory)

    assert len(backend.render(request()).tokens) == 2
    assert len(backend.render(request(ass=b"other")).tokens) == 2
    assert len(created) == 2 and created[0].closed and not created[1].closed

    backend.close()
    backend.close()
    assert created[1].closed


def test_backend_forwards_mpv_margin_contract() -> None:
    native = FakeRenderer(
        Result(
            (
                Layer(1, 1, b"\xff", 0x01020300, 10, 20),
                Layer(1, 1, b"\xff", 0x04050600, 30, 40),
            )
        )
    )
    backend = LibassGeometryBackend(renderer_factory=lambda *_args, **_kwargs: native)

    backend.render(request(margins=(98, 99, 0, 0), use_margins=True))

    assert native.calls == [
        (
            (1_250, (1280, 720), (1280, 720)),
            {
                "pixel_aspect": 1.0,
                "margins": (98, 99, 0, 0),
                "use_margins": True,
            },
        )
    ]
