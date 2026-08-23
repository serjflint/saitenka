from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from saitenka.subtitles import (
    FontProvider,
    FontSetup,
    GeometryPaletteEntry,
    GeometryRequest,
    SubtitleEventId,
    SubtitleFrameId,
    SubtitleTrackId,
)
from saitenka.subtitles.geometry import MAX_GEOMETRY_TOKENS
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
    font_setup: FontSetup | None = None,
    palette_size: int = 2,
    keep_coverage: bool = False,
) -> GeometryRequest:
    track = SubtitleTrackId("track")
    event = SubtitleEventId(track, 1_000, 2_000, 0, 0)
    return GeometryRequest(
        generation,
        track,
        SubtitleFrameId(track, (event,)),
        1_250,
        (1280, 720),
        (1280, 720),
        ass,
        margins=margins,
        use_margins=use_margins,
        palette=(
            GeometryPaletteEntry(event, 0, 0x010203),
            GeometryPaletteEntry(event, 1, 0x040506),
        )[:palette_size],
        reserved_rgb=(0xFFFFFF,),
        attachments=(("font.ttf", b"font"),),
        font_setup=font_setup or FontSetup(),
        render_profile=render_profile,
        keep_coverage=keep_coverage,
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


def test_extractor_orders_flattened_tokens_across_distinct_authored_events() -> None:
    baseline = request()
    first = baseline.frame_id.active_event_ids[0]
    second = SubtitleEventId(baseline.track_id, 1_100, 1_900, 1, 1)
    multi = replace(
        baseline,
        frame_id=SubtitleFrameId(baseline.track_id, (first, second)),
        palette=(
            GeometryPaletteEntry(second, 1, 0x040506),
            GeometryPaletteEntry(first, 0, 0x010203),
        ),
    )

    geometry = extract_token_geometry(
        Result(
            (
                Layer(1, 1, b"\xff", 0x04050600, 30, 40),
                Layer(1, 1, b"\xff", 0x01020300, 10, 20),
            )
        ),
        multi,
    )

    assert [(item.event_id, item.token_index) for item in geometry] == [(first, 0), (second, 1)]


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


def test_extractor_rejects_two_token_colors_on_the_same_pixel() -> None:
    rendered = Result(
        (
            Layer(1, 1, b"\xff", 0x01020300, 10, 20),
            Layer(1, 1, b"\xff", 0x04050600, 10, 20),
        )
    )

    with pytest.raises(ValueError, match="overlap"):
        extract_token_geometry(rendered, request())


def test_extractor_rejects_character_pixels_outside_frame() -> None:
    rendered = Result(
        (
            Layer(1, 1, b"\xff", 0x01020300, 1280, 20),
            Layer(1, 1, b"\xff", 0x04050600, 30, 40),
        )
    )

    with pytest.raises(ValueError, match="outside the frame"):
        extract_token_geometry(rendered, request())


def test_request_cache_identity_covers_render_inputs_but_not_generation() -> None:
    baseline = request()

    assert baseline.cache_key() == request(generation=9).cache_key()
    assert baseline.cache_key() != request(ass=b"changed").cache_key()
    assert baseline.cache_key() != request(margins=(10, 20, 30, 40)).cache_key()
    assert baseline.cache_key() != request(use_margins=True).cache_key()
    assert baseline.cache_key() != request(render_profile=(("sub-scale", "1.2"),)).cache_key()
    # Not a render input but a content one: a hit served maskless to a caller that wanted coverage
    # drops the cue to the plainest color device with nothing saying why.
    assert baseline.cache_key() != request(keep_coverage=True).cache_key()


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

    with pytest.raises(ValueError, match="requested frame"):
        GeometryRequest(
            baseline.generation,
            baseline.track_id,
            baseline.frame_id,
            baseline.timestamp_ms,
            baseline.frame_size,
            baseline.storage_size,
            baseline.ass,
            palette=(GeometryPaletteEntry(other_event, 0, 1),),
        )


def test_request_rejects_frame_pixel_budget_before_rendering() -> None:
    assert replace(request(), frame_size=(4096, 4096)).frame_size == (4096, 4096)

    with pytest.raises(ValueError, match="frame pixel limit"):
        replace(request(), frame_size=(4097, 4097))


def test_request_rejects_palette_budget_before_rendering() -> None:
    baseline = request()
    event = baseline.frame_id.active_event_ids[0]
    accepted = tuple(
        GeometryPaletteEntry(event, index, index + 1) for index in range(MAX_GEOMETRY_TOKENS)
    )
    assert len(replace(baseline, palette=accepted).palette) == MAX_GEOMETRY_TOKENS
    palette = tuple(
        GeometryPaletteEntry(event, index, index + 1) for index in range(MAX_GEOMETRY_TOKENS + 1)
    )

    with pytest.raises(ValueError, match="palette entry limit"):
        replace(baseline, palette=palette)


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

    def library_version(self) -> int:
        return 0x1705000


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


def test_backend_forwards_every_font_source_to_the_renderer() -> None:
    """The measuring renderer has to be handed the same four sources mpv's was, or it lays the cue
    out in whatever the system happens to offer and every box comes out the wrong width."""
    seen: list[dict] = []

    def factory(_ass, **kwargs):
        seen.append(kwargs)
        return FakeRenderer(Result((Layer(1, 1, b"\xff", 0x01020300, 10, 20),)))

    backend = LibassGeometryBackend(renderer_factory=factory)
    setup = FontSetup(
        fonts_dir="/config/fonts",
        extract_fonts=True,
        default_font="/config/subfont.ttf",
        default_family="sans-serif",
        fontconfig_config="/config/fonts.conf",
        font_provider=FontProvider.FONTCONFIG,
    )

    backend.render(request(font_setup=setup, palette_size=1))

    assert seen == [
        {
            "fonts": [("font.ttf", b"font")],
            "library_path": None,
            "fonts_dir": "/config/fonts",
            "extract_fonts": True,
            "default_font": "/config/subfont.ttf",
            "default_family": "sans-serif",
            "fontconfig_config": "/config/fonts.conf",
            "font_provider": 3,
            "features": [],
        }
    ]


def test_a_font_source_change_is_a_different_cached_renderer() -> None:
    """Two tracks whose only difference is the font set must not share a renderer: libass reads the
    directory and the extraction flag before it parses, so a reused one is measuring the old set."""
    plain = request(palette_size=1)
    attached = request(palette_size=1, font_setup=FontSetup(fonts_dir="/config/fonts"))

    assert plain.cache_key() != attached.cache_key()


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
                "max_bitmap_bytes": 1_843_200,
                "style": None,
            },
        )
    ]
