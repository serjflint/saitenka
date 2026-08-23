"""libasslite adapter for hidden token geometry."""

from __future__ import annotations

import importlib
import time
from array import array
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from saitenka import otel_metrics
from saitenka.subtitles.geometry import MAX_BITMAP_BYTES, GeometrySnapshot, Rect, TokenGeometry

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from saitenka.subtitles.document import SubtitleEventId
    from saitenka.subtitles.geometry import GeometryRequest


class ImageLayer(Protocol):
    width: int
    height: int
    bitmap: bytes
    color: int
    dst_x: int
    dst_y: int
    image_type: int


class RenderResult(Protocol):
    layers: Sequence[ImageLayer]


class NativeRenderer(Protocol):
    def render(
        self,
        timestamp_ms: int,
        frame_size: tuple[int, int],
        storage_size: tuple[int, int],
        *,
        pixel_aspect: float,
        margins: tuple[int, int, int, int],
        use_margins: bool,
        max_bitmap_bytes: int,
    ) -> RenderResult: ...

    def close(self) -> None: ...

    def library_version(self) -> int: ...


class RendererFactory(Protocol):
    def __call__(  # noqa: PLR0913  # one keyword per libass font-setup call; see FontSetup
        self,
        ass: bytes,
        *,
        fonts: list[tuple[str, bytes]],
        library_path: Path | None,
        fonts_dir: str | None,
        extract_fonts: bool,
        default_font: str | None,
        default_family: str | None,
        font_provider: int,
        fontconfig_config: str | None,
    ) -> NativeRenderer: ...


@dataclass(frozen=True, slots=True)
class _TokenKey:
    event_id: SubtitleEventId
    token_index: int
    rgb: int


def _collect_layer(
    layer: ImageLayer,
    palette: dict[int, tuple[int, _TokenKey]],
    reserved: set[int],
    owners: array,
    frame_size: tuple[int, int],
    bounds: dict[_TokenKey, list[int]],
    segments: dict[_TokenKey, list[Rect]],
) -> None:
    if layer.image_type != 0 or layer.width <= 0 or layer.height <= 0:
        return
    rgb = layer.color >> 8
    if rgb in reserved:
        return
    if rgb not in palette:
        raise ValueError(f"unknown libass character color: {rgb:#08x}")
    owner, key = palette[rgb]
    if len(layer.bitmap) != layer.width * layer.height:
        raise ValueError("libass character bitmap has an invalid size")
    frame_width, frame_height = frame_size
    painted = False
    for offset, coverage in enumerate(layer.bitmap):
        if not coverage:
            continue
        x = layer.dst_x + offset % layer.width
        y = layer.dst_y + offset // layer.width
        if not 0 <= x < frame_width or not 0 <= y < frame_height:
            raise ValueError("libass character bitmap extends outside the frame")
        position = y * frame_width + x
        previous = owners[position]
        if previous not in {0, owner}:
            raise ValueError("ambiguous libass token overlap")
        owners[position] = owner
        extent = bounds.setdefault(key, [x, y, x + 1, y + 1])
        extent[0] = min(extent[0], x)
        extent[1] = min(extent[1], y)
        extent[2] = max(extent[2], x + 1)
        extent[3] = max(extent[3], y + 1)
        painted = True
    if painted:
        segments[key].append(Rect(layer.dst_x, layer.dst_y, layer.width, layer.height))


def _validate_token_pixels(
    palette: dict[int, tuple[int, _TokenKey]], bounds: dict[_TokenKey, list[int]]
) -> list[_TokenKey]:
    def order(item: _TokenKey) -> tuple[int, int]:
        return item.token_index, item.event_id.source_order

    missing = {key for _owner, key in palette.values()} - set(bounds)
    if missing:
        raise ValueError(f"missing libass token colors: {sorted(missing, key=order)}")
    return sorted(bounds, key=order)


def _token_geometry(key: _TokenKey, extent: list[int], regions: list[Rect]) -> TokenGeometry:
    left, top, right, bottom = extent
    return TokenGeometry(
        key.event_id,
        key.token_index,
        Rect(left, top, right - left, bottom - top),
        tuple(regions),
    )


def extract_token_geometry(
    result: RenderResult,
    request: GeometryRequest,
) -> tuple[TokenGeometry, ...]:
    """Recover every requested token from public character-image layers."""
    palette = {
        entry.rgb: (index, _TokenKey(entry.event_id, entry.token_index, entry.rgb))
        for index, entry in enumerate(request.palette, start=1)
    }
    reserved = set(request.reserved_rgb)
    owners = array("H", [0]) * (request.frame_size[0] * request.frame_size[1])
    bounds: dict[_TokenKey, list[int]] = {}
    segments: dict[_TokenKey, list[Rect]] = defaultdict(list)
    for layer in result.layers:
        _collect_layer(layer, palette, reserved, owners, request.frame_size, bounds, segments)
    ordered = _validate_token_pixels(palette, bounds)
    return tuple(_token_geometry(key, bounds[key], segments[key]) for key in ordered)


class LibassGeometryBackend:
    """Bounded renderer cache over the optional ``libasslite`` package."""

    def __init__(
        self,
        *,
        library_path: Path | None = None,
        renderer_cache_max: int = 3,
        renderer_factory: RendererFactory | None = None,
    ) -> None:
        if renderer_cache_max <= 0:
            raise ValueError("renderer cache bound must be positive")
        self._library_path = library_path
        self._cache_max = renderer_cache_max
        self._factory = renderer_factory
        self._renderers: OrderedDict[str, NativeRenderer] = OrderedDict()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _new_renderer(self, request: GeometryRequest) -> NativeRenderer:
        factory = self._factory
        if factory is None:
            module = importlib.import_module("libasslite")
            factory = cast("RendererFactory", module.AssRenderer)
        setup = request.font_setup
        return factory(
            request.ass,
            fonts=list(request.attachments),
            library_path=self._library_path,
            fonts_dir=setup.fonts_dir,
            extract_fonts=setup.extract_fonts,
            default_font=setup.default_font,
            default_family=setup.default_family,
            font_provider=int(setup.font_provider),
            fontconfig_config=setup.fontconfig_config,
        )

    def _renderer(self, request: GeometryRequest) -> NativeRenderer:
        key = request.cache_key()
        renderer = self._renderers.pop(key, None)
        if renderer is None:
            renderer = self._new_renderer(request)
        self._renderers[key] = renderer
        while len(self._renderers) > self._cache_max:
            _key, evicted = self._renderers.popitem(last=False)
            evicted.close()
        return renderer

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        if self._closed:
            raise RuntimeError("libass geometry backend is closed")
        if not request.palette:
            raise ValueError("hit-map request needs a token palette")
        renderer = self._renderer(request)
        with otel_metrics.traced("subtitle_geometry_libass") as span:
            span.set("provider", "libasslite")
            span.set("libass_version", f"0x{renderer.library_version():x}")
            span.set("timestamp_ms", request.timestamp_ms)
            started = time.perf_counter_ns()
            result = renderer.render(
                request.timestamp_ms,
                request.frame_size,
                request.storage_size,
                pixel_aspect=request.pixel_aspect,
                margins=request.margins,
                use_margins=request.use_margins,
                max_bitmap_bytes=min(
                    2 * request.frame_size[0] * request.frame_size[1], MAX_BITMAP_BYTES
                ),
            )
            render_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("render_ms", render_ms)
            span.set("layer_count", len(result.layers))
            if otel_metrics.subtitle_geometry_render_ms is not None:
                otel_metrics.subtitle_geometry_render_ms.record(render_ms)
            started = time.perf_counter_ns()
            tokens = extract_token_geometry(result, request)
            extract_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("extract_ms", extract_ms)
            span.set("found_tokens", len(tokens))
            if otel_metrics.subtitle_geometry_extract_ms is not None:
                otel_metrics.subtitle_geometry_extract_ms.record(extract_ms)
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.frame_id,
            request.timestamp_ms,
            request.variant,
            tokens,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        while self._renderers:
            _key, renderer = self._renderers.popitem(last=False)
            renderer.close()
