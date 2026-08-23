"""libasslite adapter for hidden token geometry."""

from __future__ import annotations

import importlib
import time
from array import array
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from saitenka import otel_metrics
from saitenka.subtitles.geometry import (
    MAX_BITMAP_BYTES,
    GeometrySnapshot,
    Rect,
    RendererState,
    TokenGeometry,
)

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
        style: object | None = None,
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
        features: list[tuple[int, bool]],
    ) -> NativeRenderer: ...


@dataclass(frozen=True, slots=True)
class _TokenKey:
    event_id: SubtitleEventId
    token_index: int
    rgb: int
    font_name: str = ""
    font_size: float = 0.0


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


def _token_geometry(
    key: _TokenKey, extent: list[int], regions: list[Rect], coverage: bytes = b""
) -> TokenGeometry:
    left, top, right, bottom = extent
    return TokenGeometry(
        key.event_id,
        key.token_index,
        Rect(left, top, right - left, bottom - top),
        tuple(regions),
        key.font_name,
        key.font_size,
        coverage,
    )


def _token_coverage(
    layers: Sequence[ImageLayer],
    palette: dict[int, tuple[int, _TokenKey]],
    bounds: dict[_TokenKey, list[int]],
) -> dict[_TokenKey, bytes]:
    """Each token's coverage, cropped to the extent the first pass measured.

    A second pass over the same layers rather than a wider first one: the crop is only knowable
    once the extent is, and buffering every painted pixel to avoid re-walking would cost more than
    the walk. Both passes are on the geometry worker, never the interaction loop.

    `max` on overlap, not `+`: two layers of one token (a glyph split across images) meet at their
    shared edge, and adding there would push the alpha past opaque and print a seam.
    """
    masks = {
        key: bytearray((extent[2] - extent[0]) * (extent[3] - extent[1]))
        for key, extent in bounds.items()
    }
    for layer in layers:
        if layer.image_type != 0 or layer.width <= 0 or layer.height <= 0:
            continue
        entry = palette.get(layer.color >> 8)
        if entry is not None and (extent := bounds.get(entry[1])) is not None:
            _blit_coverage(layer, masks[entry[1]], extent)
    return {key: bytes(mask) for key, mask in masks.items()}


def _blit_coverage(layer: ImageLayer, mask: bytearray, extent: list[int]) -> None:
    stride = extent[2] - extent[0]
    for offset, value in enumerate(layer.bitmap):
        if not value:
            continue
        x = layer.dst_x + offset % layer.width - extent[0]
        y = layer.dst_y + offset // layer.width - extent[1]
        position = y * stride + x
        mask[position] = max(mask[position], value)


def extract_token_geometry(
    result: RenderResult,
    request: GeometryRequest,
    *,
    keep_coverage: bool = False,
) -> tuple[TokenGeometry, ...]:
    """Recover every requested token from public character-image layers.

    `keep_coverage` also keeps the anti-aliased mask each token was measured from, which is what the
    raster device paints when the text device cannot reach the face. Off by default: a snapshot that
    carries masks nobody will tint is bytes crossing a thread for nothing.
    """
    palette = {
        entry.rgb: (
            index,
            _TokenKey(
                entry.event_id, entry.token_index, entry.rgb, entry.font_name, entry.font_size
            ),
        )
        for index, entry in enumerate(request.palette, start=1)
    }
    reserved = set(request.reserved_rgb)
    owners = array("H", [0]) * (request.frame_size[0] * request.frame_size[1])
    bounds: dict[_TokenKey, list[int]] = {}
    segments: dict[_TokenKey, list[Rect]] = defaultdict(list)
    for layer in result.layers:
        _collect_layer(layer, palette, reserved, owners, request.frame_size, bounds, segments)
    ordered = _validate_token_pixels(palette, bounds)
    masks = _token_coverage(result.layers, palette, bounds) if keep_coverage else {}
    return tuple(
        _token_geometry(key, bounds[key], segments[key], masks.get(key, b"")) for key in ordered
    )


#: `ASS_OverrideBits` (`ass.h`), read off the header rather than counted: the neighbouring values
#: are not in the order the names suggest, and `1 << 9` — one bit below JUSTIFY — is
#: `ASS_OVERRIDE_FULL_STYLE`, which replaces every field of every style with the one handed over.
#: Only these two, and only when the option is set: a wider override restyles the cue away from the
#: document instead of carrying the two fields the document cannot state.
_ASS_OVERRIDE_BIT_JUSTIFY = 1 << 10
_ASS_OVERRIDE_BIT_BLUR = 1 << 11


def _render_style(state: RendererState) -> object | None:
    """`libasslite.RenderStyle` for this frame, or `None` when libass's defaults already say it.

    Imported here rather than at module scope: the wrapper is an optional extra, and this module is
    imported by hosts that never installed it.
    """
    if state == RendererState():
        return None
    module = importlib.import_module("libasslite")
    bits = 0
    if state.blur:
        bits |= _ASS_OVERRIDE_BIT_BLUR
    if state.justify:
        bits |= _ASS_OVERRIDE_BIT_JUSTIFY
    if not bits:
        return module.RenderStyle(font_scale=state.font_scale)
    return module.RenderStyle(
        font_scale=state.font_scale,
        override_bits=bits,
        override_style=module.AssStyle(blur=state.blur, justify=state.justify),
    )


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
            features=list(request.renderer_state.features),
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
                style=_render_style(request.renderer_state),
            )
            render_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("render_ms", render_ms)
            span.set("layer_count", len(result.layers))
            if otel_metrics.subtitle_geometry_render_ms is not None:
                otel_metrics.subtitle_geometry_render_ms.record(render_ms)
            started = time.perf_counter_ns()
            tokens = extract_token_geometry(result, request, keep_coverage=request.keep_coverage)
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
