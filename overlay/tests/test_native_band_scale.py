"""Stage 1 of the scale-boundary rewrite (``vibe/crisp-scale-boundary-plan.md``): the raster leaf takes
a ``scale``.

The body glyph leaf (``draw_token`` → ``render_flow_window`` → ``render_layout_window`` →
``raster_body_window``) can now draw a band at native device resolution: the 1× layout position is
projected by ``scale`` and each glyph drawn at ``size×scale`` — a crisp native mask, not an upscale.

The oracle is platform-independent (FreeType AA is not byte-stable across builds, so no native pixel
golden): (1) the band's scan/link boxes stay in REFERENCE px — scale-invariant, equal to the 1× band's;
(2) the native band's SIZE is ``round(w×scale) × round(h×scale)``; (3) downscaling the native band by
``scale`` matches the 1× band within a tight MAE (native is a faithful scaled render, ~1.0 measured).
"""

from __future__ import annotations

import util
from overlay.model import Span, Style
from overlay.render.flow import layout_flow, render_flow, render_flow_window
from overlay.render.layout import Block
from PIL import Image

_WIDTH, _PAD = 300, 8


def _laid():
    """A CJK flow with an inline underlined cross-reference link → scan cells AND a link box."""
    flow = [
        Span("追いかけると同義語は"),
        Span("見る", Style(underline=True), "?query=見る"),
        Span("。長い説明文が続く。長い説明文が続く。"),
    ]
    return layout_flow(flow, Block(width=_WIDTH, padding=_PAD))


def test_native_band_geometry_is_scale_invariant():
    lay = _laid()
    h = lay.height
    s1: list = []
    l1: list = []
    render_flow_window(lay, 0, h, s1, l1, scale=1.0)
    s2: list = []
    l2: list = []
    render_flow_window(lay, 0, h, s2, l2, scale=2.0)
    assert s1 and l1  # not vacuous — the flow really has scan cells and a link
    assert s2 == s1  # scan boxes come back in reference px regardless of raster scale
    assert l2 == l1  # …and so do link boxes — hit geometry is scale-free by construction


def test_native_band_size_is_the_scaled_reference_size():
    lay = _laid()
    h = lay.height
    ref = render_flow_window(lay, 0, h, scale=1.0)
    native = render_flow_window(lay, 0, h, scale=2.0)
    assert ref.size == (_WIDTH + 2 * _PAD, h)
    assert native.size == (round((_WIDTH + 2 * _PAD) * 2), round(h * 2))


def test_downscaled_native_band_matches_the_reference_band():
    # No native pixel golden (FreeType AA varies by build); instead assert the native raster is a
    # FAITHFUL scaled render — downscaled by the scale it matches the 1× band within a tight MAE.
    lay = _laid()
    h = lay.height
    ref = render_flow_window(lay, 0, h, scale=1.0)
    native = render_flow_window(lay, 0, h, scale=2.0)
    down = native.resize(ref.size, Image.Resampling.LANCZOS)
    assert util.mae(down, ref) < 5.0  # ~1.0 measured — comfortably a faithful scaled render


def test_full_flow_native_render_is_a_faithful_scaled_render():
    # The non-body raster leaf (render_flow full path, Stage 2b) — headers/chips render natively at
    # scale instead of upscaling their 1× image. Native size = scaled reference; downscale ≈ 1×.
    flow = [Span("見出し語", Style(size=40, weight=700)), Span("  タグ", Style(size=20))]
    block = Block(width=300, padding=8)
    ref = render_flow(flow, block)
    native = render_flow(flow, block, scale=2.0)
    assert native.size == (round(ref.width * 2), round(ref.height * 2))
    down = native.resize(ref.size, Image.Resampling.LANCZOS)
    assert util.mae(down, ref) < 8.0
