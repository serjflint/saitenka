"""Regression tests for the NATIVE (scale>1) band + BGRA caches (the one-panel crisp path).

The scroll-jank bug these guard: the native compositor originally re-converted every band's full-width
BGRA on EVERY frame (no memo, unlike the 1× ``_bgra`` path) → ~40 ms scroll frames at 4K. These pin the
memo contract — converted once, reused, evicted in tandem with its band, never stale, and byte-identical
warm-vs-cold — plus scale isolation and render-loop safety.
"""

from __future__ import annotations

import numpy as np
import pytest
import util

from saitenka.model import Theme
from saitenka.panel import panel_rows, render_panel
from saitenka.render import banded
from saitenka.render.banded import WindowedPanel

_W, _VH = 384, 240
#: Every channel distinct, unlike ``Theme().bg`` = (252, 252, 250, 255) whose color channels are
#: near-equal. A background fill that transposes or drops a channel is invisible against the default.
_ODD_BG = (11, 22, 33, 255)


def _panel(theme: Theme | None = None):
    entry = util.cjk_links_entry(8)  # tall enough to span many bands → real eviction pressure
    wp = WindowedPanel(panel_rows(entry, _W), _W, theme)
    total = render_panel(entry, width=_W).height
    return wp, total


def _measure(wp: WindowedPanel, total: int) -> None:
    for s in range(0, max(1, total - _VH) + 1, 40):  # walk the whole panel → offset table exact
        wp.viewport(s, _VH)


def test_native_bgra_memo_never_outlives_its_band():
    wp, total = _panel()
    wp._scaled_cap = 4  # force eviction while scrolling
    _measure(wp, total)
    for s in range(0, max(1, total - _VH) + 1, 90):  # scroll the whole panel at native scale
        wp.viewport_bgra(s, _VH, scale=2.0)
    assert len(wp._scaled_blocks) <= 4  # band cache bounded
    assert set(wp._scaled_bgra) <= set(wp._scaled_blocks)  # a memo entry never outlives its band


def test_native_compose_keeps_scales_isolated(monkeypatch):
    from saitenka import bgra

    convert, conversions = bgra.to_bgra_array, []

    def counted(image):
        conversions.append(None)
        return convert(image)

    monkeypatch.setattr(bgra, "to_bgra_array", counted)
    wp, total = _panel()
    _measure(wp, total)
    one_x = wp.viewport_bgra(0, _VH)
    first = wp.viewport_bgra(0, _VH, scale=1.5)
    second = wp.viewport_bgra(0, _VH, scale=2.0)
    cold, cold_total = _panel()
    _measure(cold, cold_total)
    cold_second = cold.viewport_bgra(0, _VH, scale=2.0)
    before_return = len(conversions)
    assert before_return > 0
    banded.guard_main_render(on=True)
    try:
        first_again = wp.viewport_bgra(0, _VH, scale=1.5)
    finally:
        banded.guard_main_render(on=False)
    one_x_again = wp.viewport_bgra(0, _VH)
    assert first.shape != second.shape
    assert np.array_equal(second, cold_second) and np.array_equal(first, first_again)
    assert np.array_equal(one_x, one_x_again) and wp.last_frame_rasters == 0
    assert len(conversions) == before_return


def test_evicted_native_band_recomposes_byte_identical():
    # A dropped memo must re-convert to the SAME pixels — no stale-cache corruption after eviction.
    wp, total = _panel()
    wp._scaled_cap = 3
    _measure(wp, total)
    top = wp.viewport_bgra(0, _VH, scale=2.0).copy()
    wp.viewport_bgra(max(0, total - _VH), _VH, scale=2.0)  # scroll away → top bands + memo evicted
    again = wp.viewport_bgra(0, _VH, scale=2.0)  # top re-rastered + re-memoised
    assert np.array_equal(top, again)  # identical — the memo is a pure accelerator


def test_native_raster_on_the_render_loop_raises_when_armed():
    # The fail-fast guard: when armed, any NATIVE raster on the render loop (main process, main thread)
    # raises — crisp rasterisation must run on a worker. The test IS the main thread, so a non-warm_only
    # native compose (which rasters) trips it.
    wp, total = _panel()
    _measure(wp, total)
    banded.guard_main_render(on=True)
    try:
        with pytest.raises(RuntimeError, match="render loop"):
            wp.viewport_bgra(0, _VH, scale=2.0)  # rasters native bands → guard trips
    finally:
        banded.guard_main_render(
            on=False
        )  # never leak the armed state to other (order-random) tests
    wp.viewport_bgra(0, _VH, scale=2.0)  # disarmed → renders fine


def test_warm_only_never_rasters_on_the_render_loop_even_when_armed():
    # The main-thread path is warm_only: a cold viewport composites cached bands only (misses → bg),
    # NEVER a raster — so it's safe on the render loop even with the guard armed.
    wp, total = _panel()
    _measure(wp, total)
    banded.guard_main_render(on=True)
    try:
        out = wp.viewport_bgra(0, _VH, scale=2.0, warm_only=True)  # no raster → no raise
    finally:
        banded.guard_main_render(on=False)
    assert out.shape == (round(_VH * 2.0), round(_W * 2.0), 4)  # a full (mostly-bg) native frame


def test_a_cold_native_frame_is_filled_with_the_theme_background():
    """What a warm_only native compose paints where it has no band — the whole frame, here.

    The only other test through this path asserts `out.shape`, so the pixels it writes were
    unchecked: the native compose is the one the crisp (hi-dpi) scroll frame runs, and a viewport it
    has no bands for is pure background.
    """
    wp, total = _panel(Theme(bg=_ODD_BG))
    _measure(wp, total)  # warms the 1x bands; the native ones stay cold
    r, g, b, a = _ODD_BG
    background = np.array([b, g, r, a], np.uint8)  # premultiplied BGRA at alpha 255

    out = wp.viewport_bgra(0, _VH, scale=2.0, warm_only=True)

    assert np.array_equal(out, np.broadcast_to(background, out.shape))
