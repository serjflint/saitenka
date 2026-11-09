"""Qualify optional character attribution against the semantic render's actual ink."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from saitenka_subtitles.character_scan import disjoint_characters
from saitenka_subtitles.geometry import CharacterGeometry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka_subtitles.geometry import GeometryRequest, Rect, TokenGeometry
    from saitenka_subtitles.libass_backend import ImageLayer


def _support(layers: Sequence[ImageLayer], colors: set[int], bounds: Rect) -> np.ndarray:
    mask = np.zeros((bounds.height + 2, bounds.width + 2), dtype=bool)
    for layer in layers:
        if layer.image_type != 0 or layer.color >> 8 not in colors or layer.color & 255:
            continue
        pixels = np.frombuffer(layer.bitmap, dtype=np.uint8).reshape(layer.height, layer.width)
        ys, xs = np.nonzero(pixels)
        xs = xs + layer.dst_x - bounds.x + 1
        ys = ys + layer.dst_y - bounds.y + 1
        if (
            np.any(xs < 0)
            or np.any(ys < 0)
            or np.any(xs >= mask.shape[1])
            or np.any(ys >= mask.shape[0])
        ):
            raise ValueError("character support moved outside semantic bounds")
        mask[ys, xs] = True
    return mask


def same_support(a: np.ndarray, b: np.ndarray) -> bool:
    """Bidirectional one-pixel support agreement, including the interior of a glyph run."""

    def dilated(mask):
        padded = np.pad(mask, 1)
        h, w = mask.shape
        result = np.zeros_like(mask)
        for y in range(3):
            for x in range(3):
                np.logical_or(result, padded[y : y + h, x : x + w], out=result)
        return result

    return bool(a.any() and b.any() and not (a & ~dilated(b)).any() and not (b & ~dilated(a)).any())


def _cell(item: TokenGeometry, start: int) -> CharacterGeometry:
    bounds = item.bounds
    clipped = []
    for rect in item.regions:
        x, y = max(rect.x, bounds.x), max(rect.y, bounds.y)
        right = min(rect.x + rect.width, bounds.x + bounds.width)
        bottom = min(rect.y + rect.height, bounds.y + bounds.height)
        if x < right and y < bottom:
            clipped.append(type(bounds)(x, y, right - x, bottom - y))
    return CharacterGeometry(item.token_index - start, bounds, tuple(clipped))


def attach_characters(
    request: GeometryRequest,
    tokens: tuple[TokenGeometry, ...],
    fine: tuple[TokenGeometry, ...],
    original_layers: Sequence[ImageLayer],
    fine_layers: Sequence[ImageLayer],
) -> tuple[TokenGeometry, ...]:
    mapping = request.character_map
    assert mapping is not None
    colors = {(entry.event_id, entry.token_index): entry.rgb for entry in request.palette}
    fine_colors = {(entry.event_id, entry.token_index): entry.rgb for entry in mapping.palette}
    spans = {span.token_index: span for span in mapping.semantic_spans}
    out = []
    for token in tokens:
        span = spans.get(token.token_index)
        if span is None:
            out.append(token)
            continue
        parts = [
            item
            for item in fine
            if item.event_id == token.event_id
            and span.text_start <= item.token_index < span.text_end
        ]
        original = _support(
            original_layers, {colors[token.event_id, token.token_index]}, token.bounds
        )
        combined = _support(
            fine_layers,
            {fine_colors[item.event_id, item.token_index] for item in parts},
            token.bounds,
        )
        characters = tuple(_cell(item, span.text_start) for item in parts)
        out.append(
            replace(token, characters=characters)
            if disjoint_characters(characters) and same_support(original, combined)
            else token
        )
    return tuple(out)
