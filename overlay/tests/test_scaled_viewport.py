"""Stage 2 of the scale-boundary rewrite: ``WindowedPanel.viewport(scale=)`` composites a NATIVE viewport.

The compositor gains a crisp native path (``scale`` > 1) that assembles a ``round(width×scale) ×
round(view_h×scale)`` device buffer from native bands — a SEPARATE code path and cache (``_scaled_blocks``)
from the 1× hot path, so ``scale=1.0`` is byte-identical (the whole existing render suite is the guard).
Bands within a row are placed by cumulative device height, so they tile seam-exactly.

Oracles (platform-independent — FreeType AA isn't byte-stable, so no native pixel golden):
1. native viewport size is the scaled reference size;
2. ``viewport_bgra(scale)`` is byte-identical to ``to_bgra_array(viewport(scale))`` (the two device paths agree);
3. downscaling the native viewport matches the 1× viewport within a tight MAE (metamorphic);
4. the scaled composite leaves the 1×/geometry state untouched (hit geometry is scale-free).
"""

from __future__ import annotations

import numpy as np
import util
from PIL import Image

from overlay.bgra import to_bgra_array
from overlay.panel import panel_rows, render_panel
from overlay.render.banded import WindowedPanel

_W, _VH = 384, 260


def _panel():
    entry = util.cjk_links_entry(4)
    return WindowedPanel(panel_rows(entry, _W), _W), render_panel(entry, width=_W).height


def _warm_1x(wp: WindowedPanel, total: int) -> None:
    for s in range(0, max(1, total - _VH) + 1, 40):
        wp.viewport(s, _VH)


def test_native_viewport_is_the_scaled_reference_size():
    wp, _total = _panel()
    assert wp.viewport(0, _VH).size == (_W, _VH)
    assert wp.viewport(0, _VH, scale=2.0).size == (round(_W * 2), round(_VH * 2))


def test_viewport_bgra_matches_the_rgba_composite_at_scale():
    wp, _total = _panel()
    img = wp.viewport(0, _VH, scale=2.0)
    bgra = wp.viewport_bgra(0, _VH, scale=2.0)
    assert bgra.shape == (round(_VH * 2), round(_W * 2), 4)
    assert np.array_equal(bgra, to_bgra_array(img))  # the two device paths agree byte-for-byte


def test_downscaled_native_viewport_matches_the_reference_viewport():
    wp, total = _panel()
    _warm_1x(wp, total)
    ref = wp.viewport(0, _VH)
    native = wp.viewport(0, _VH, scale=2.0)
    down = native.resize(ref.size, Image.Resampling.LANCZOS)
    assert util.mae(down.convert("RGBA"), ref.convert("RGBA")) < 8.0  # ~2.0 measured


def test_scaled_composite_leaves_hit_geometry_scale_free():
    # The native path must not disturb the 1× geometry — scan boxes are identical whether or not a
    # scaled viewport was composited (geometry is reference px, the seam invariant).
    wp, total = _panel()
    _warm_1x(wp, total)
    before = wp.scan_boxes()
    wp.viewport(0, _VH, scale=2.0)
    wp.viewport(0, _VH, scale=1.5)
    assert wp.scan_boxes() == before  # unchanged by native compositing
