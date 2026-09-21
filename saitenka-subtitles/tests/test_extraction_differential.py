"""The vectorised geometry extraction against the per-pixel loop it replaced."""

from __future__ import annotations

import random
from array import array
from collections import defaultdict

import numpy as np
import pytest
from saitenka_subtitles.geometry import Rect
from saitenka_subtitles.libass_backend import _collect_layer, _TokenKey

FRAME = (64, 48)


class Layer:
    def __init__(self, width, height, bitmap, color, dst_x, dst_y, image_type=0) -> None:
        self.width, self.height, self.bitmap = width, height, bitmap
        self.color, self.dst_x, self.dst_y, self.image_type = color, dst_x, dst_y, image_type


def reference_collect(layer, palette, reserved, owners, frame_size, bounds, segments) -> None:
    """The per-pixel original, verbatim."""
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


def key_for(index: int) -> _TokenKey:
    from saitenka_subtitles.document import SubtitleEventId, SubtitleTrackId

    return _TokenKey(SubtitleEventId(SubtitleTrackId("t"), 0, 1, 0, 0), index, 0x010000 + index)


def layer_for(rng: random.Random) -> Layer:
    """One generated layer. Seeded rather than Hypothesis: this package ships on its own and its dev
    group is `pytest` alone, so a property-testing dependency here is a distribution decision this
    test does not need to make. The sweep below covers the same ground, reproducibly."""
    width = rng.randint(1, 9)
    height = rng.randint(1, 9)
    # Mostly zeros on purpose: the loop skips them and the vector form must skip exactly the same
    # ones, and a blank top row is what puts a glyph's ink below its bitmap origin.
    bitmap = bytes(rng.choice((0, 0, 0, rng.randint(1, 255))) for _ in range(width * height))
    return Layer(
        width,
        height,
        bitmap,
        (0x010000 + rng.randint(0, 2)) << 8,
        rng.randint(0, FRAME[0] - width),
        rng.randint(0, FRAME[1] - height),
    )


@pytest.mark.parametrize("seed", range(200))
def test_the_vectorised_collect_agrees_with_the_loop_it_replaced(seed: int) -> None:
    rng = random.Random(seed)
    drawn = [layer_for(rng) for _ in range(rng.randint(1, 6))]
    palette = {0x010000 + index: (index + 1, key_for(index)) for index in range(3)}

    def run(collect, owner_store):
        bounds: dict = {}
        segments: dict = defaultdict(list)
        error = None
        try:
            for layer in drawn:
                collect(layer, palette, set(), owner_store, FRAME, bounds, segments)
        except ValueError as exc:
            error = str(exc)
        return bounds, dict(segments), error

    fast_bounds, fast_segments, fast_error = run(
        _collect_layer, np.zeros(FRAME[0] * FRAME[1], dtype=np.uint16)
    )
    slow_bounds, slow_segments, slow_error = run(
        reference_collect, array("H", [0]) * (FRAME[0] * FRAME[1])
    )

    assert fast_error == slow_error
    if fast_error is not None:
        # Partial state is NOT compared, and the difference is real: the loop stopped at the first
        # offending pixel with some of the layer already written, the vector form tests the layer
        # whole and writes nothing. Both refuse the same render, and `resolve_outcome` throws the
        # accumulator away — so agreeing here would mean pinning which pixel got blamed.
        return
    assert (fast_bounds, fast_segments) == (slow_bounds, slow_segments)


@pytest.mark.parametrize(
    ("dst_x", "dst_y"), [(-1, 0), (0, -1), (FRAME[0] - 1, 0), (0, FRAME[1] - 1)]
)
def test_a_bitmap_outside_the_frame_is_still_refused(dst_x: int, dst_y: int) -> None:
    """The refusal survived the rewrite, and its message did — a caller distinguishes this from an
    overlap only by the text."""
    layer = Layer(4, 4, b"\xff" * 16, 0x01000000, dst_x, dst_y)

    with pytest.raises(ValueError, match="outside the frame"):
        _collect_layer(
            layer,
            {0x010000: (1, key_for(0))},
            set(),
            np.zeros(FRAME[0] * FRAME[1], dtype=np.uint16),
            FRAME,
            {},
            defaultdict(list),
        )


def test_two_tokens_sharing_a_pixel_are_still_refused() -> None:
    """The overlap check is what makes a measured box trustworthy; vectorised it tests the whole
    layer at once, and it still has to bite."""
    owners = np.zeros(FRAME[0] * FRAME[1], dtype=np.uint16)
    palette = {0x010000: (1, key_for(0)), 0x010001: (2, key_for(1))}
    first = Layer(2, 2, b"\xff" * 4, 0x01000000, 5, 5)
    second = Layer(2, 2, b"\xff" * 4, 0x01000100, 5, 5)

    _collect_layer(first, palette, set(), owners, FRAME, {}, defaultdict(list))

    with pytest.raises(ValueError, match="ambiguous libass token overlap"):
        _collect_layer(second, palette, set(), owners, FRAME, {}, defaultdict(list))
