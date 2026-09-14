"""Recover native ink using colored layers only as topological ownership hints."""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np

from saitenka_subtitles.geometry import MAX_BITMAP_BYTES, Rect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka_subtitles.geometry import GeometryRequest, TokenGeometry
    from saitenka_subtitles.libass_backend import ImageLayer

MAX_ATTRIBUTION_PIXELS = MAX_BITMAP_BYTES // 8
MAX_ATTRIBUTION_RUNS = 65_536
MAX_ATTRIBUTION_MASK_BYTES = MAX_BITMAP_BYTES


class AttributionFailure(StrEnum):
    OVERLAPPING_FILL = "overlapping native fill layers"
    UNKNOWN_HINT = "unknown hint color"
    OVERLAPPING_HINTS = "overlapping hint owners"
    RUN_BUDGET = "run budget exhausted"
    AMBIGUOUS = "ambiguous connected ink"
    UNOWNED = "native ink has no owner"
    MISSING_OWNER_INK = "token has no native ink"
    MISSING_FILL = "missing fill layers"
    OUTSIDE_FRAME = "layer outside frame"
    PIXEL_BUDGET = "pixel budget exhausted"
    MASK_BUDGET = "aggregate mask budget exhausted"


class NativeAttributionError(ValueError):
    def __init__(self, reason: AttributionFailure) -> None:
        self.reason = reason
        super().__init__(f"native mask attribution: {reason}")


def _fill_layers(layers: Sequence[ImageLayer]) -> list[ImageLayer]:
    return [
        layer for layer in layers if layer.image_type == 0 and layer.width > 0 and layer.height > 0
    ]


def _window(layer: ImageLayer, extent: Rect) -> tuple[slice, slice]:
    return (
        slice(layer.dst_y - extent.y, layer.dst_y - extent.y + layer.height),
        slice(layer.dst_x - extent.x, layer.dst_x - extent.x + layer.width),
    )


def _planes(
    native: Sequence[ImageLayer],
    hints: Sequence[ImageLayer],
    extent: Rect,
    palette: dict[int, int],
    reserved: set[int],
) -> tuple[np.ndarray, np.ndarray]:
    ink = np.zeros((extent.height, extent.width), dtype=np.uint8)
    owners = np.zeros(ink.shape, dtype=np.int16)
    for layer in native:
        pixels = np.frombuffer(layer.bitmap, dtype=np.uint8).reshape(layer.height, layer.width)
        target = ink[_window(layer, extent)]
        if np.any((target != 0) & (pixels != 0)):
            raise NativeAttributionError(AttributionFailure.OVERLAPPING_FILL)
        np.maximum(target, pixels, out=target)
    for layer in hints:
        rgb = layer.color >> 8
        owner = -1 if rgb in reserved else palette.get(rgb)
        if owner is None:
            raise NativeAttributionError(AttributionFailure.UNKNOWN_HINT)
        pixels = np.frombuffer(layer.bitmap, dtype=np.uint8).reshape(layer.height, layer.width) != 0
        hint_target = owners[_window(layer, extent)]
        if np.any(pixels & (hint_target != 0) & (hint_target != owner)):
            raise NativeAttributionError(AttributionFailure.OVERLAPPING_HINTS)
        hint_target[pixels] = owner
    return ink, owners


def _root(parents: list[int], index: int) -> int:
    while parents[index] != index:
        parents[index] = parents[parents[index]]
        index = parents[index]
    return index


def _components(ink: np.ndarray, owners: np.ndarray) -> tuple[np.ndarray, dict[int, list[int]]]:
    parents: list[int] = []
    identities: list[set[int]] = []
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []
    for y in range(ink.shape[0]):
        row = (ink[y] != 0) | (owners[y] != 0)
        edges = np.flatnonzero(np.diff(np.pad(row.astype(np.int8), (1, 1))))
        current = []
        cursor = 0
        for start, end in zip(edges[::2], edges[1::2], strict=True):
            if len(parents) >= MAX_ATTRIBUTION_RUNS:
                raise NativeAttributionError(AttributionFailure.RUN_BUDGET)
            index = len(parents)
            parents.append(index)
            identities.append(set(map(int, np.unique(owners[y, start:end]))) - {0})
            while cursor < len(previous) and previous[cursor][1] < start:
                cursor += 1
            for offset in range(cursor, len(previous)):
                left, _right, prior = previous[offset]
                if left > end:
                    break
                root = _root(parents, prior)
                if root != index:
                    identities[index].update(identities[root])
                    parents[root] = index
            if len(identities[index]) > 1:
                raise NativeAttributionError(AttributionFailure.AMBIGUOUS)
            current.append((int(start), int(end), index))
            runs.append((y, int(start), int(end), index))
        previous = current
    attributed = np.zeros(ink.shape, dtype=np.int16)
    bounds: dict[int, list[int]] = {}
    for y, start, end, index in runs:
        identity = identities[_root(parents, index)]
        if not identity:
            raise NativeAttributionError(AttributionFailure.UNOWNED)
        owner = next(iter(identity))
        attributed[y, start:end] = owner
        extent = bounds.setdefault(owner, [start, y, end, y + 1])
        extent[0] = min(extent[0], start)
        extent[2] = max(extent[2], end)
        extent[3] = y + 1
    return attributed, bounds


def _native_token(
    token: TokenGeometry,
    owner: int,
    extent: Rect,
    region: list[int],
    ink: np.ndarray,
    attributed: np.ndarray,
) -> TokenGeometry:
    left, top, right, bottom = region
    pixels = ink[top:bottom, left:right]
    selected = (attributed[top:bottom, left:right] == owner) & (pixels != 0)
    ys, xs = np.nonzero(selected)
    if not len(xs):
        raise NativeAttributionError(AttributionFailure.MISSING_OWNER_INK)
    x, y, end_x, end_y = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    bounds = Rect(extent.x + left + x, extent.y + top + y, end_x - x, end_y - y)
    mask = np.where(selected[y:end_y, x:end_x], pixels[y:end_y, x:end_x], 0)
    return replace(token, bounds=bounds, regions=(bounds,), coverage=mask.tobytes())


def attribute_native_masks(
    native_layers: Sequence[ImageLayer],
    hint_layers: Sequence[ImageLayer],
    request: GeometryRequest,
    tokens: tuple[TokenGeometry, ...],
) -> tuple[TokenGeometry, ...]:
    native, hints = _fill_layers(native_layers), _fill_layers(hint_layers)
    layers = native + hints
    if not native or not hints:
        raise NativeAttributionError(AttributionFailure.MISSING_FILL)
    left, top = min(layer.dst_x for layer in layers), min(layer.dst_y for layer in layers)
    right = max(layer.dst_x + layer.width for layer in layers)
    bottom = max(layer.dst_y + layer.height for layer in layers)
    if left < 0 or top < 0 or right > request.frame_size[0] or bottom > request.frame_size[1]:
        raise NativeAttributionError(AttributionFailure.OUTSIDE_FRAME)
    extent = Rect(left, top, right - left, bottom - top)
    if extent.width * extent.height > MAX_ATTRIBUTION_PIXELS:
        raise NativeAttributionError(AttributionFailure.PIXEL_BUDGET)
    palette = {entry.rgb: index for index, entry in enumerate(request.palette, 1)}
    ink, owners = _planes(native, hints, extent, palette, set(request.reserved_rgb))
    attributed, regions = _components(ink, owners)
    if (
        sum(
            (right - left) * (bottom - top)
            for owner, (left, top, right, bottom) in regions.items()
            if owner > 0
        )
        > MAX_ATTRIBUTION_MASK_BYTES
    ):
        raise NativeAttributionError(AttributionFailure.MASK_BUDGET)
    identities = {
        (entry.event_id, entry.token_index): index for index, entry in enumerate(request.palette, 1)
    }
    return tuple(
        _native_token(token, owner, extent, regions[owner], ink, attributed)
        for token in tokens
        for owner in (identities[token.event_id, token.token_index],)
    )
