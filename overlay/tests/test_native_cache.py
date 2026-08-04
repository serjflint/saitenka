"""Regression tests for the NATIVE (scale>1) band + BGRA caches (the one-panel crisp path).

The scroll-jank bug these guard: the native compositor originally re-converted every band's full-width
BGRA on EVERY frame (no memo, unlike the 1× ``_bgra`` path) → ~40 ms scroll frames at 4K. These pin the
memo contract — converted once, reused, evicted in tandem with its band, never stale, and byte-identical
warm-vs-cold — plus the separation invariants (per-scale keys, native path doesn't disturb the 1× caches).
"""

from __future__ import annotations

import numpy as np
import pytest
import util

from overlay.panel import panel_rows, render_panel
from overlay.render import banded
from overlay.render.banded import WindowedPanel

_W, _VH = 384, 240


def _panel():
    entry = util.cjk_links_entry(8)  # tall enough to span many bands → real eviction pressure
    wp = WindowedPanel(panel_rows(entry, _W), _W)
    total = render_panel(entry, width=_W).height
    return wp, total


def _measure(wp: WindowedPanel, total: int) -> None:
    for s in range(0, max(1, total - _VH) + 1, 40):  # walk the whole panel → offset table exact
        wp.viewport(s, _VH)


def test_native_bgra_is_memoised_and_reused_across_frames():
    wp, total = _panel()
    _measure(wp, total)
    wp.viewport_bgra(0, _VH, scale=2.0)  # first native compose → converts + memoises each band
    snap = dict(wp._scaled_bgra)
    assert snap  # the memo is populated
    wp.viewport_bgra(0, _VH, scale=2.0)  # second identical compose
    for k, arr in snap.items():
        assert wp._scaled_bgra[k] is arr  # SAME array object → not re-converted (the jank fix)


def test_native_bgra_memo_never_outlives_its_band():
    wp, total = _panel()
    wp._scaled_cap = 4  # force eviction while scrolling
    _measure(wp, total)
    for s in range(0, max(1, total - _VH) + 1, 90):  # scroll the whole panel at native scale
        wp.viewport_bgra(s, _VH, scale=2.0)
    assert len(wp._scaled_blocks) <= 4  # band cache bounded
    assert set(wp._scaled_bgra) <= set(wp._scaled_blocks)  # a memo entry never outlives its band


def test_native_compose_caches_bands_per_scale_without_collision():
    wp, total = _panel()
    _measure(wp, total)
    wp.viewport_bgra(0, _VH, scale=1.5)
    wp.viewport_bgra(0, _VH, scale=2.0)
    skeys = {k[2] for k in wp._scaled_blocks}
    assert skeys == {1.5, 2.0}  # bands cached per scale key — 1.5 and 2.0 never collide


def test_evicted_native_band_recomposes_byte_identical():
    # A dropped memo must re-convert to the SAME pixels — no stale-cache corruption after eviction.
    wp, total = _panel()
    wp._scaled_cap = 3
    _measure(wp, total)
    top = wp.viewport_bgra(0, _VH, scale=2.0).copy()
    wp.viewport_bgra(max(0, total - _VH), _VH, scale=2.0)  # scroll away → top bands + memo evicted
    again = wp.viewport_bgra(0, _VH, scale=2.0)  # top re-rastered + re-memoised
    assert np.array_equal(top, again)  # identical — the memo is a pure accelerator


def test_native_compose_populates_the_native_band_cache_not_the_1x_one():
    # The native path is SEPARATE from the 1× band cache: its body bands land in _scaled_blocks keyed by
    # scale, never in the 1× _blocks (whose keys are 2-tuples). Guards the "1× hot path untouched" claim.
    wp, total = _panel()
    _measure(wp, total)
    wp.viewport_bgra(0, _VH, scale=2.0)
    assert wp._scaled_blocks  # native bands cached here
    assert all(len(k) == 3 for k in wp._scaled_blocks)  # (row, band, scale)
    assert all(len(k) == 2 for k in wp._blocks)  # the 1× cache keeps its (row, band) keys


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


def test_1x_bgra_memo_still_reused_after_the_shared_counter_change():
    # Regression: the native memo now shares the bgra_memo otel counters with the 1× path; the 1× memo
    # itself must still reuse its converted bands (a warm scroll frame is row-copies, not re-converts).
    wp, total = _panel()
    _measure(wp, total)
    wp.viewport_bgra(0, _VH)
    snap = dict(wp._bgra)
    assert snap
    wp.viewport_bgra(0, _VH)
    for k, arr in snap.items():
        assert wp._bgra[k] is arr  # 1× per-band BGRA still memoised (unchanged)
