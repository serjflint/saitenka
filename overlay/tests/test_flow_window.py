"""``render_flow(y_window=(y0, y1))`` must be pixel-identical to the full render cropped to that band.

This is the primitive the windowed block cache (PR3) stands on: materialise a viewport slice of a
pathologically tall block without drawing the rest. Identity vs the crop is the whole contract — if it
holds, the composite/golden path is unchanged and only the render cost moves.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from overlay.model import Span, Style
from overlay.render.flow import render_flow
from overlay.render.layout import Block

S = Style(size=23)


def _tall_flow(n: int = 60) -> list[Span]:
    # Many spans wrap to many lines → a tall block worth windowing into. Deterministic content.
    return [Span("これは長い定義の本文でありスクロールが必要になる説明です。", S) for _ in range(n)]


def _block() -> Block:
    return Block(width=320, background=(0, 0, 0, 0))


def _full():
    return render_flow(_tall_flow(), _block())


def test_full_window_equals_the_whole_render():
    full = _full()
    win = render_flow(_tall_flow(), _block(), y_window=(0, full.height))
    assert np.array_equal(np.asarray(win), np.asarray(full))


@given(data=st.data())
@settings(max_examples=40, deadline=None)
def test_window_is_pixel_identical_to_the_full_crop(data):
    full = _full()
    h = full.height
    y0 = data.draw(st.integers(0, max(0, h - 1)))
    y1 = data.draw(st.integers(y0 + 1, h))
    win = render_flow(_tall_flow(), _block(), y_window=(y0, y1))
    ref = full.crop((0, y0, full.width, y1))
    assert win.size == ref.size
    assert np.array_equal(np.asarray(win), np.asarray(ref))


@pytest.mark.parametrize("band", [(0, 1), (0, 5), (7, 8)])
def test_window_at_exact_boundaries_matches_the_crop(band):
    full = _full()
    y0, y1 = band
    win = render_flow(_tall_flow(), _block(), y_window=(y0, y1))
    ref = full.crop((0, y0, full.width, y1))
    assert np.array_equal(np.asarray(win), np.asarray(ref))


def test_window_draws_only_overlapping_lines_cheaply():
    # A tiny mid-block window renders a small image regardless of the full block height.
    full = _full()
    mid = full.height // 2
    win = render_flow(_tall_flow(), _block(), y_window=(mid, mid + 40))
    assert win.height == 40
    assert win.height < full.height
