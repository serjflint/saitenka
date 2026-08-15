"""libasslite adapter for hidden token geometry."""

from __future__ import annotations

import importlib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.subtitles.geometry import GeometrySnapshot, Rect, TokenGeometry

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

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
    ) -> RenderResult: ...

    def close(self) -> None: ...


class RendererFactory(Protocol):
    def __call__(
        self,
        ass: bytes,
        *,
        fonts: list[tuple[str, bytes]],
        library_path: Path | None,
    ) -> NativeRenderer: ...


@dataclass(frozen=True, slots=True, order=True)
class _TokenKey:
    token_index: int
    rgb: int


def _collect_layer(
    layer: ImageLayer,
    palette: dict[int, _TokenKey],
    reserved: set[int],
    pixels: dict[_TokenKey, set[tuple[int, int]]],
    segments: dict[_TokenKey, list[Rect]],
) -> None:
    if layer.image_type != 0 or layer.width <= 0 or layer.height <= 0:
        return
    rgb = layer.color >> 8
    if rgb in reserved:
        return
    if rgb not in palette:
        raise ValueError(f"unknown libass character color: {rgb:#08x}")
    key = palette[rgb]
    if len(layer.bitmap) != layer.width * layer.height:
        raise ValueError("libass character bitmap has an invalid size")
    points = {
        (layer.dst_x + offset % layer.width, layer.dst_y + offset // layer.width)
        for offset, coverage in enumerate(layer.bitmap)
        if coverage
    }
    if points:
        pixels[key].update(points)
        segments[key].append(Rect(layer.dst_x, layer.dst_y, layer.width, layer.height))


def _validate_token_pixels(
    palette: dict[int, _TokenKey], pixels: dict[_TokenKey, set[tuple[int, int]]]
) -> list[_TokenKey]:
    missing = set(palette.values()) - set(pixels)
    if missing:
        raise ValueError(f"missing libass token colors: {sorted(missing)}")
    ordered = sorted(pixels)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if pixels[left] & pixels[right]:
                raise ValueError(f"ambiguous libass token overlap: {left} and {right}")
    return ordered


def _token_geometry(
    key: _TokenKey, points: set[tuple[int, int]], regions: list[Rect]
) -> TokenGeometry:
    left = min(x for x, _y in points)
    top = min(y for _x, y in points)
    right = max(x for x, _y in points) + 1
    bottom = max(y for _x, y in points) + 1
    return TokenGeometry(
        key.token_index, Rect(left, top, right - left, bottom - top), tuple(regions)
    )


def extract_token_geometry(
    result: RenderResult,
    request: GeometryRequest,
) -> tuple[TokenGeometry, ...]:
    """Recover every requested token from public character-image layers."""
    palette = {entry.rgb: _TokenKey(entry.token_index, entry.rgb) for entry in request.palette}
    reserved = set(request.reserved_rgb)
    pixels: dict[_TokenKey, set[tuple[int, int]]] = defaultdict(set)
    segments: dict[_TokenKey, list[Rect]] = defaultdict(list)
    for layer in result.layers:
        _collect_layer(layer, palette, reserved, pixels, segments)
    ordered = _validate_token_pixels(palette, pixels)
    return tuple(_token_geometry(key, pixels[key], segments[key]) for key in ordered)


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

    def _new_renderer(self, request: GeometryRequest) -> NativeRenderer:
        factory = self._factory
        if factory is None:
            module = importlib.import_module("libasslite")
            factory = module.AssRenderer
        return factory(
            request.ass,
            fonts=list(request.attachments),
            library_path=self._library_path,
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
        result = self._renderer(request).render(
            request.timestamp_ms,
            request.frame_size,
            request.storage_size,
            pixel_aspect=request.pixel_aspect,
        )
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.event_id,
            request.timestamp_ms,
            request.variant,
            extract_token_geometry(result, request),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        while self._renderers:
            _key, renderer = self._renderers.popitem(last=False)
            renderer.close()
