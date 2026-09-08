"""libasslite adapter for hidden token geometry."""

from __future__ import annotations

import importlib
import logging
import time
from array import array
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast

from saitenka_subtitles.document import SubtitleEventId, SubtitleFrameId, SubtitleTrackId
from saitenka_subtitles.fragments import (
    ROW_PITCH,
    FragmentRequest,
    ProbeLayout,
    fragments_from,
    probe_colour_count,
    probe_document,
    probe_rows,
)
from saitenka_subtitles.geometry import (
    MAX_BITMAP_BYTES,
    GeometryPaletteEntry,
    GeometrySnapshot,
    Rect,
    RendererState,
    TokenGeometry,
)
from saitenka_subtitles.telemetry import (
    EXTRACT_MS,
    RENDER_MS,
    RENDERER_BUILD_MS,
    NullTelemetry,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

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

#: Text, face, size, letter spacing, horizontal scale — every input to the probe render. Two tokens
#: with one word and one face but different run metrics land differently, so they must not share a
#: measured anchor.
_AnchorKey = tuple[str, str, float, float, float, bool, bool]


@dataclass(frozen=True, slots=True)
class _Anchor:
    """A measured correction: the token's own origin, plus its glyphs' when it is drawn per glyph."""

    dx: int
    dy: int
    glyph_dx: tuple[int, ...] = ()
    glyph_dy: tuple[int, ...] = ()


#: How many measured anchors to keep. One per distinct (word, face, frame size); a film's vocabulary
#: is far smaller, and the bound exists for the resize case rather than the reading one.
ANCHOR_CACHE_MAX = 4096
#: The probe's own event identity and colour range. Distinct from anything a cue uses, because the
#: probe document is rendered through the same token-recovery code as a real frame.
_PROBE_RGB_BASE = 0x010000


def _probe_event(track_id: SubtitleTrackId) -> SubtitleEventId:
    """The probe document's single event identity, on the track being rendered — a frame's events
    must belong to its own track, and the probe rides the request's."""
    return SubtitleEventId(track_id, 0, 1, 0, 0)


def _probe_rgbs(count: int) -> list[int]:
    return [_PROBE_RGB_BASE + index for index in range(count)]


@dataclass(frozen=True, slots=True)
class _TokenKey:
    event_id: SubtitleEventId
    token_index: int
    rgb: int
    font_name: str = ""
    font_size: float = 0.0
    spacing: float = 0.0
    scale_x: float = 100.0
    bold: bool = False
    italic: bool = False


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
        spacing=key.spacing,
        scale_x=key.scale_x,
        bold=key.bold,
        italic=key.italic,
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
                entry.event_id,
                entry.token_index,
                entry.rgb,
                entry.font_name,
                entry.font_size,
                entry.spacing,
                entry.scale_x,
                entry.bold,
                entry.italic,
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
        #: A renderer per font environment kept for anchor probes, so pointing one at a probe
        #: document never evicts the cue document from the renderer the cue is being drawn with.
        self._probes: OrderedDict[str, NativeRenderer] = OrderedDict()
        self._probe_failed = False
        #: Keys a probe could not measure. `extract_token_geometry` refuses a palette colour that
        #: drew nothing, so one inkless token — text the overprint would refuse anyway — fails the
        #: whole batch; remembering them keeps that from re-rendering once per frame forever.
        self._unprobeable: set[_AnchorKey] = set()
        #: `(text, face, size) -> (dx, dy)`, least-recently-used last. The offset is a property of
        #: exactly those three, so a repeated cue and a recurring word cost a dict lookup.
        #:
        #: Bounded because the size is the frame's, not the document's: `_palette_in_frame_units`
        #: multiplies by `frame_height / play_res_y`, so dragging a window edge mints a distinct
        #: float per pixel of height and every one of them misses. Unbounded, a resize would leave
        #: an entry per (word, transient size) for the life of the session.
        self._anchors: OrderedDict[_AnchorKey, _Anchor] = OrderedDict()
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

    def _probe_geometry(
        self,
        request: GeometryRequest,
        layout: ProbeLayout,
        frame: tuple[int, int],
    ) -> list[tuple[int, int, int, int, int]]:
        """Ink rectangles for the probe document, through this font environment's probe renderer."""
        key = request.renderer_key()
        probe_event = _probe_event(request.track_id)
        encoded = layout.document.encode()
        renderer = self._probes.get(key)
        if renderer is None:
            renderer = self._new_renderer(replace(request, ass=encoded))
            self._probes[key] = renderer
            while len(self._probes) > self._cache_max:
                _evicted_key, evicted = self._probes.popitem(last=False)
                evicted.close()
        else:
            self._probes.move_to_end(key)
            renderer.set_document(encoded, list(request.renderer_state.features))
        probe = replace(
            request,
            ass=encoded,
            frame_size=frame,
            storage_size=frame,
            frame_id=SubtitleFrameId(request.track_id, (probe_event,)),
            palette=tuple(
                GeometryPaletteEntry(probe_event, ordinal, rgb, "", 0.0, "")
                for ordinal, rgb in enumerate(_probe_rgbs(len(layout.slots)))
            ),
            reserved_rgb=(),
            keep_coverage=False,
        )
        result = renderer.render(
            # The probe's events start at zero; rendering at the cue's timestamp would silently draw
            # nothing once a file runs past their end.
            0,
            frame,
            frame,
            pixel_aspect=1.0,
            margins=(0, 0, 0, 0),
            use_margins=False,
            max_bitmap_bytes=min(2 * frame[0] * frame[1], MAX_BITMAP_BYTES),
            # The cue's own style, not libass's defaults: hinting and blur change a glyph's extent,
            # so measuring without them would return an offset for a render nobody performs.
            style=_render_style(request.renderer_state),
        )
        return [
            (
                token.token_index,
                token.bounds.x,
                token.bounds.y,
                token.bounds.width,
                token.bounds.height,
            )
            for token in extract_token_geometry(result, probe, keep_coverage=False)
        ]

    def _anchored(
        self, request: GeometryRequest, tokens: tuple[TokenGeometry, ...]
    ) -> tuple[TokenGeometry, ...]:
        """`tokens` carrying the offset a redraw of each needs, measured once per text/face/size."""
        texts = {
            (entry.event_id, entry.token_index): entry.text
            for entry in request.palette
            if entry.text and entry.font_name and entry.font_size > 0
        }
        keyed = [
            (
                token,
                (
                    text,
                    token.font_name,
                    token.font_size,
                    token.spacing,
                    token.scale_x,
                    token.bold,
                    token.italic,
                ),
            )
            for token in tokens
            if (text := texts.get((token.event_id, token.token_index)))
        ]
        unseen = [
            (token, key)
            for token, key in keyed
            if key not in self._anchors and key not in self._unprobeable
        ]
        if unseen:
            self._measure_anchors(request, [(token, key) for token, key in unseen])
        offsets = {}
        for token, key in keyed:
            offset = self._anchors.get(key)
            if offset is not None:
                self._anchors.move_to_end(key)
            # By event AND index: a frame may hold several events, and `_validate_palette` only
            # requires the pair to be unique. Keyed on the index alone, two events whose first
            # tokens differ would hand each other's correction over.
            offsets[token.event_id, token.token_index] = offset
        return tuple(
            replace(
                token,
                anchor_dx=anchor.dx,
                anchor_dy=anchor.dy,
                glyph_dx=anchor.glyph_dx,
                glyph_dy=anchor.glyph_dy,
            )
            if (anchor := offsets.get((token.event_id, token.token_index)))
            else token
            for token in tokens
        )

    def _measure_anchors(
        self, request: GeometryRequest, unseen: list[tuple[TokenGeometry, _AnchorKey]]
    ) -> None:
        """Render the fragmented layout the overprint will ask for and record where its ink lands.

        A probe that fails is not an error: the offsets stay unrecorded, every redraw keeps the
        origin it used before, and the cue is drawn exactly as it is today.
        """
        requests = [
            FragmentRequest(index, key[0], key[1], key[2], key[3], key[4], key[5], key[6])
            for index, (_token, key) in enumerate(unseen)
        ]
        pitch = max(int(max(key[2] for _token, key in unseen)), 1) + ROW_PITCH
        frame = (request.frame_size[0], max(pitch * probe_rows(requests), 1))
        layout = probe_document(requests, _probe_rgbs(probe_colour_count(requests)), frame)
        try:
            measured = self._probe_geometry(request, layout, frame)
        except Exception:
            # Falling back to the measured origin is the safe answer for a layout libass cannot
            # satisfy, and the wrong answer to silence with. The first failure is loud because a
            # probe that never succeeds disables the correction for the whole session with nothing
            # on screen to show it; the rest are quiet so a bad face cannot flood the log.
            log.log(
                logging.DEBUG if self._probe_failed else logging.WARNING,
                "anchor probe failed; the overprint keeps its measured origin",
                exc_info=True,
            )
            self._probe_failed = True
            # Without this the batch is retried on every geometry render for the rest of the
            # session: nothing records the attempt, so `unseen` keeps returning the same keys.
            self._unprobeable.update(key for _token, key in unseen)
            return
        for index, fragment in fragments_from(measured, requests, layout).items():
            self._anchors[unseen[index][1]] = _Anchor(
                fragment.dx, fragment.dy, fragment.glyph_dx, fragment.glyph_dy
            )
        while len(self._anchors) > ANCHOR_CACHE_MAX:
            self._anchors.popitem(last=False)

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        if self._closed:
            raise RuntimeError("libass geometry backend is closed")
        if not request.palette:
            raise ValueError("hit-map request needs a token palette")
        renderer, built_ms = self._renderer(request)
        with self._telemetry.span("subtitle_geometry_libass") as span:
            span.set("provider", "libasslite")
            span.set("renderer_built_ms", built_ms)
            span.set("renderer_cache_size", len(self._renderers))
            if built_ms:
                self._telemetry.record(RENDERER_BUILD_MS, built_ms)
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
            self._telemetry.record(RENDER_MS, render_ms)
            started = time.perf_counter_ns()
            tokens = self._anchored(
                request,
                extract_token_geometry(result, request, keep_coverage=request.keep_coverage),
            )
            extract_ms = (time.perf_counter_ns() - started) / 1_000_000
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
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._documents.clear()
        self._anchors.clear()
        self._unprobeable.clear()
        while self._probes:
            _probe_key, probe = self._probes.popitem(last=False)
            probe.close()
        while self._renderers:
            _key, renderer = self._renderers.popitem(last=False)
            renderer.close()
