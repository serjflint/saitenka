"""libasslite adapter for hidden token geometry."""

from __future__ import annotations

import importlib
import logging
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np

from saitenka_subtitles import whole_cue
from saitenka_subtitles.geometry import (
    MAX_BITMAP_BYTES,
    GeometrySnapshot,
    PaintQualification,
    Rect,
    RendererState,
    TokenGeometry,
)
from saitenka_subtitles.telemetry import (
    EXTRACT_COLLECT_MS,
    EXTRACT_MS,
    EXTRACT_OWNERS_MS,
    EXTRACT_VALIDATE_MS,
    RENDER_MS,
    RENDERER_BUILD_MS,
    NullTelemetry,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from saitenka_subtitles.document import SubtitleEventId
    from saitenka_subtitles.geometry import GeometryRequest
    from saitenka_subtitles.telemetry import GeometryTelemetry


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

    def set_document(self, ass: bytes, features: list[tuple[int, bool]] = ..., /) -> None:
        """Point this renderer at a different track, keeping its library and glyph cache."""
        ...

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


log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TokenKey:
    event_id: SubtitleEventId
    token_index: int
    rgb: int


def _collect_layer(
    layer: ImageLayer,
    palette: dict[int, tuple[int, _TokenKey]],
    reserved: set[int],
    owners: np.ndarray | None,
    frame_size: tuple[int, int],
    bounds: dict[_TokenKey, list[int]],
    segments: dict[_TokenKey, list[Rect]],
) -> None:
    """Attribute one layer's painted pixels to the token whose color it carries.

    Whole-array rather than per pixel. This ran once per painted pixel of every glyph and was ~97%
    of a geometry render — measured at 11.2 ms of an 11.5 ms extraction, against libass's own render
    at 0.07 ms — which put a cue's measurement above the rate cues arrive at, so the prefetch queue
    dropped 18 of 31 lookahead renders and the color reached the screen late.

    The three refusals below are unchanged in meaning and only widened in scope: they now test the
    whole layer at once instead of stopping at the first offending pixel. Nothing depends on which
    pixel was blamed — the render fails either way — and the extent is the same union.
    """
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
    rows, columns = np.nonzero(
        np.frombuffer(layer.bitmap, dtype=np.uint8).reshape(layer.height, layer.width)
    )
    if not rows.size:
        return
    xs = columns.astype(np.intp) + layer.dst_x
    ys = rows.astype(np.intp) + layer.dst_y
    frame_width, frame_height = frame_size
    left, right = int(xs.min()), int(xs.max())
    top, bottom = int(ys.min()), int(ys.max())
    if left < 0 or right >= frame_width or top < 0 or bottom >= frame_height:
        raise ValueError("libass character bitmap extends outside the frame")
    if owners is not None:
        positions = ys * frame_width + xs
        previous = owners[positions]
        if bool(np.any((previous != 0) & (previous != owner))):
            raise ValueError("ambiguous libass token overlap")
        owners[positions] = owner
    extent = bounds.setdefault(key, [left, top, right + 1, bottom + 1])
    extent[0] = min(extent[0], left)
    extent[1] = min(extent[1], top)
    extent[2] = max(extent[2], right + 1)
    extent[3] = max(extent[3], bottom + 1)
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
    *,
    telemetry: GeometryTelemetry | None = None,
) -> tuple[TokenGeometry, ...]:
    """Recover every requested token from public character-image layers.

    `telemetry` splits this function's own cost four ways. It is ~99% of a geometry render — libass's
    render is ~0.1 ms against ~12 ms here — so one number for it said only "the slow part is ours",
    which is not an answer anyone can act on. Optional so every existing caller is unchanged.
    """
    sink = telemetry or NullTelemetry()
    palette = {
        entry.rgb: (
            index,
            _TokenKey(entry.event_id, entry.token_index, entry.rgb),
        )
        for index, entry in enumerate(request.palette, start=1)
    }
    reserved = set(request.reserved_rgb)
    # Frame-sized and zeroed on every render: 1920x1080 is ~2M entries, 4 MB, per cue. Timed apart
    # because an allocation that scales with the FRAME rather than with the cue is a different
    # problem from a loop that scales with the ink, and one number could not tell them apart.
    started = time.perf_counter_ns()
    owners = None
    sink.record(EXTRACT_OWNERS_MS, (time.perf_counter_ns() - started) / 1_000_000)

    bounds: dict[_TokenKey, list[int]] = {}
    segments: dict[_TokenKey, list[Rect]] = defaultdict(list)
    started = time.perf_counter_ns()
    for layer in result.layers:
        _collect_layer(layer, palette, reserved, owners, request.frame_size, bounds, segments)
    sink.record(EXTRACT_COLLECT_MS, (time.perf_counter_ns() - started) / 1_000_000)

    started = time.perf_counter_ns()
    ordered = _validate_token_pixels(palette, bounds)
    sink.record(EXTRACT_VALIDATE_MS, (time.perf_counter_ns() - started) / 1_000_000)

    return tuple(_token_geometry(key, bounds[key], segments[key]) for key in ordered)


def _render_style(state: RendererState) -> object | None:
    """`libasslite.RenderStyle` for this frame, or `None` when libass's defaults already say it.

    Imported here rather than at module scope: the wrapper is an optional extra, and this module is
    imported by hosts that never installed it.

    `ASS_OverrideBits` is not in the order its names suggest — the value one bit below JUSTIFY is
    `FULL_STYLE`, which replaces every field of every style with the one handed over and collapses
    the layout. Taking the mask from `libasslite` rather than restating it here is what keeps that
    from being re-derived by counting.

    SELECTIVE_FONT_SCALE carries no `override_style` field of its own: it does not copy anything
    from the style, it decides whether `font_scale` reaches a positioned event at all.
    """
    if state == RendererState():
        return None
    module = importlib.import_module("libasslite")
    bits = module.OverrideBits.DEFAULT
    if state.blur:
        bits |= module.OverrideBits.BLUR
    if state.justify:
        bits |= module.OverrideBits.JUSTIFY
    if state.selective_font_scale:
        bits |= module.OverrideBits.SELECTIVE_FONT_SCALE
    setters = {
        "font_scale": state.font_scale,
        "line_position": state.line_position,
        "line_spacing": state.line_spacing,
        "hinting": state.hinting,
    }
    if not bits:
        return module.RenderStyle(**setters)
    return module.RenderStyle(
        **setters,
        override_bits=int(bits),
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
        telemetry: GeometryTelemetry | None = None,
    ) -> None:
        if renderer_cache_max <= 0:
            raise ValueError("renderer cache bound must be positive")
        self._library_path = library_path
        self._cache_max = renderer_cache_max
        self._factory = renderer_factory
        self._telemetry: GeometryTelemetry = telemetry or NullTelemetry()
        self._renderers: OrderedDict[str, NativeRenderer] = OrderedDict()
        #: The document each cached renderer is currently pointed at, by identity rather than by
        #: value: the bytes are the cue's whole hit-map document, and comparing them per cue would
        #: cost more than the swap it is trying to avoid. The producer hands the same object back
        #: for a repeated render, so identity is the right test and a false miss only re-swaps.
        self._documents: dict[str, bytes] = {}
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

    def _renderer(self, request: GeometryRequest) -> tuple[NativeRenderer, float]:
        """This request's renderer, pointed at this request's document, and what building cost.

        Keyed on `renderer_key()` — the font environment — not `cache_key()`, which is the
        SNAPSHOT's identity and hashes the timestamp, the palette and the document. Those change
        every cue, so the renderer cache could never hit: it rebuilt libass, rescanned the font
        directory and discarded the glyph cache once per cue. One live session built a renderer for
        16 of 18 renders at 1.4–3.0ms each, against renders of 0.7–4.5ms, with the cache sitting
        pinned at its bound evicting entries nothing could reuse.

        The document is swapped on the way out rather than left to the caller: a cached renderer
        still holding the previous cue's track would hand back that cue's boxes, silently, which is
        the failure class this whole path exists to prevent.
        """
        key = request.renderer_key()
        renderer = self._renderers.pop(key, None)
        built_ms = 0.0
        if renderer is None:
            started = time.perf_counter_ns()
            with self._telemetry.span("subtitle_geometry_renderer_build") as span:
                span.set("attachments", len(request.attachments))
                span.set("document_bytes", len(request.ass))
                renderer = self._new_renderer(request)
            built_ms = (time.perf_counter_ns() - started) / 1_000_000
            self._documents[key] = request.ass
        elif self._documents.get(key) is not request.ass:
            renderer.set_document(request.ass, list(request.renderer_state.features))
            self._documents[key] = request.ass
        self._renderers[key] = renderer
        while len(self._renderers) > self._cache_max:
            evicted_key, evicted = self._renderers.popitem(last=False)
            self._documents.pop(evicted_key, None)
            evicted.close()
        return renderer, built_ms

    @staticmethod
    def _render_frame(renderer: NativeRenderer, request: GeometryRequest) -> RenderResult:
        return renderer.render(
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

    def _whole_cue(
        self, request: GeometryRequest, layers: Sequence[ImageLayer]
    ) -> whole_cue.WholeCue | None:
        cue = request.whole_cue
        if cue is None:
            return None
        palette = {entry.rgb: entry.token_index for entry in request.palette}
        cue = replace(cue, layers=whole_cue.retain_layers(layers, palette))
        cue = replace(
            cue,
            evidence=(
                ("shadow_bounds", whole_cue.layer_bounds(cue.layers)),
                ("margins", request.margins),
                ("mapping", cue.mapping),
                ("comparison", "not-run"),
            ),
        )
        if not cue.qualify or cue.osd_reason != "eligible":
            return cue
        try:
            return self._qualify_osd(request, cue, palette)
        except (ValueError, RuntimeError, OSError):
            log.debug("whole-cue OSD qualification failed", exc_info=True)
            return replace(cue, osd_reason="osd-input", blockers=(*cue.blockers, "osd-input"))

    def _qualify_osd(
        self, request: GeometryRequest, cue: whole_cue.WholeCue, palette: dict[int, int]
    ) -> whole_cue.WholeCue:
        with self._telemetry.span("subtitle_osd_qualification") as span:
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            osd_request = replace(
                request,
                ass=whole_cue.osd_document(
                    cue, tuple((token, rgb) for rgb, token in palette.items())
                ),
                timestamp_ms=0,
                margins=(0, 0, 0, 0),
                use_margins=False,
                storage_size=request.frame_size,
                renderer_state=RendererState(features=((3, True),)),
                attachments=(),
                font_setup=request.osd_font_setup
                or replace(request.font_setup, extract_fonts=False),
            )
            renderer, _built = self._renderer(osd_request)
            predicted = self._render_frame(renderer, osd_request)
            candidate = whole_cue.retain_layers(predicted.layers, palette)
            evidence = (
                ("shadow_bounds", whole_cue.layer_bounds(cue.layers)),
                ("osd_bounds", whole_cue.layer_bounds(candidate)),
                ("margins", request.margins),
                ("mapping", cue.mapping),
                ("qualification_cpu_ms", (time.thread_time_ns() - cpu_started) / 1_000_000),
                ("qualification_ms", (time.perf_counter_ns() - started) / 1_000_000),
                ("comparison", "exact" if candidate == cue.layers else "shape-mismatch"),
            )
            span.set("generation", request.generation)
            span.set("timestamp_ms", request.timestamp_ms)
            for name, values in (("shadow", cue.layers), ("osd", candidate)):
                span.set(
                    f"{name}_units",
                    tuple(
                        number
                        for layer in values[:128]
                        for number in (layer.token, layer.x, layer.y, layer.width, layer.height)
                    ),
                )
                span.set(f"{name}_units_omitted", max(0, len(values) - 128))
            for key, value in evidence:
                span.set(key, value)
            return replace(
                cue,
                evidence=evidence,
                osd_reason="eligible" if candidate == cue.layers else "shape-mismatch",
                blockers=() if candidate == cue.layers else ("shape-mismatch",),
            )

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        if self._closed:
            raise RuntimeError("libass geometry backend is closed")
        if not request.palette:
            raise ValueError("hit-map request needs a token palette")
        renderer, built_ms = self._renderer(request)
        library_version = renderer.library_version()
        with self._telemetry.span("subtitle_geometry_libass") as span:
            span.set("provider", "libasslite")
            span.set("renderer_built_ms", built_ms)
            span.set("renderer_cache_size", len(self._renderers))
            if built_ms:
                self._telemetry.record(RENDERER_BUILD_MS, built_ms)
            span.set("libass_version", f"0x{library_version:x}")
            span.set("timestamp_ms", request.timestamp_ms)
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            result = self._render_frame(renderer, request)
            span.set("render_cpu_ms", (time.thread_time_ns() - cpu_started) / 1_000_000)
            render_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("render_ms", render_ms)
            span.set("layer_count", len(result.layers))
            self._telemetry.record(RENDER_MS, render_ms)
            started = time.perf_counter_ns()
            cpu_started = time.thread_time_ns()
            tokens = extract_token_geometry(result, request, telemetry=self._telemetry)
            extract_ms = (time.perf_counter_ns() - started) / 1_000_000
            span.set("extract_cpu_ms", (time.thread_time_ns() - cpu_started) / 1_000_000)
            span.set("extract_ms", extract_ms)
            span.set("found_tokens", len(tokens))
            self._telemetry.record(EXTRACT_MS, extract_ms)
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.frame_id,
            request.timestamp_ms,
            request.variant,
            tokens,
            libass_version=library_version,
            mask_source="whole-cue-layers",
            paint_qualification=(
                PaintQualification.OCCLUDED
                if request.whole_cue is not None
                and whole_cue.has_occlusion(result.layers, {entry.rgb for entry in request.palette})
                else request.paint_qualification
            ),
            whole_cue=self._whole_cue(request, result.layers),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._documents.clear()
        while self._renderers:
            _key, renderer = self._renderers.popitem(last=False)
            renderer.close()
