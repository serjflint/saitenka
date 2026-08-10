"""Stage 4: the measure/raster split in ``body_block`` — a walked def body lays out once, then any
y-window rasters from that handle pixel-identically to the full render cropped to the band.

This is the caching contract the banded block cache stands on: ``layout_body_block`` pays the walk +
wrap once (the ~200ms cost on pathological entries), and each scroll band is an O(band) ``getmask2``
off the same handle — never a re-walk."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from overlay.body_block import (
    BodyRenderArgs,
    layout_body_block,
    raster_body_window,
    render_body_block,
)
from overlay.model import Style


def _args(n: int = 40) -> BodyRenderArgs:
    # A tall multi-paragraph body — wraps to many lines, worth windowing into bands.
    para = "これはとても長い定義の説明でありスクロールが必要になるほど縦に伸びる本文です。"
    content = [{"tag": "div", "content": para} for _ in range(n)]
    return BodyRenderArgs(
        content=content,
        body_style=Style(size=23),
        body_w=320,
        gap_px=3,
        indent_px=18,
        gutter_px=22,
    )


def test_measured_height_matches_the_full_raster_height():
    args = _args()
    laid = layout_body_block(args)
    full_img, *_ = render_body_block(args)
    assert laid.full_height == full_img.height  # measure == raster, no getmask2 in the measure


def test_full_window_is_pixel_identical_to_the_full_render():
    args = _args()
    laid = layout_body_block(args)
    full_img, *_ = render_body_block(args)
    win, _scan, _links = raster_body_window(laid, 0, laid.full_height)
    assert np.array_equal(np.asarray(win), np.asarray(full_img))


@given(data=st.data())
@settings(max_examples=30, deadline=None)
def test_any_band_is_pixel_identical_to_the_full_crop(data):
    args = _args()
    laid = layout_body_block(args)
    full_img, *_ = render_body_block(args)
    h = full_img.height
    y0 = data.draw(st.integers(0, max(0, h - 1)))
    y1 = data.draw(st.integers(y0 + 1, h))
    win, _scan, _links = raster_body_window(laid, y0, y1)
    ref = full_img.crop((0, y0, full_img.width, y1))
    assert win.size == ref.size
    assert np.array_equal(np.asarray(win), np.asarray(ref))


def test_one_layout_serves_many_band_rasters_walking_once(monkeypatch):
    # The caching contract: laying out once then rastering many bands walks the SC exactly once.
    import overlay.body_block as BB

    calls = [0]
    orig = BB.walk
    monkeypatch.setattr(
        BB,
        "walk",
        lambda node, base=None, media=None: (
            calls.__setitem__(0, calls[0] + 1),
            orig(node, base, media),
        )[1],
    )
    args = _args()
    laid = layout_body_block(args)
    assert calls[0] == 1  # the single walk
    h = laid.full_height
    for y0 in range(0, h, 128):
        raster_body_window(laid, y0, min(h, y0 + 128))
    assert calls[0] == 1  # every band reused the one layout — no re-walk


def test_band_scan_boxes_land_in_band_space():
    args = _args()
    laid = layout_body_block(args)
    y0, y1 = 200, 360
    _win, scan, _links = raster_body_window(laid, y0, y1)
    # boxes come back relative to the band top (band space), not document space (~200-360 here); each
    # overlaps [0, y1-y0) — a clipped top line starts < 0, a clipped bottom line ends past the band.
    assert scan
    for sb in scan:
        assert sb.y < y1 - y0 and sb.y + sb.h > 0  # overlaps the band, in band coords


@pytest.mark.parametrize("band", [(0, 1), (0, 4), (50, 51)])
def test_boundary_bands_match_the_crop(band):
    args = _args()
    laid = layout_body_block(args)
    full_img, *_ = render_body_block(args)
    y0, y1 = band
    win, *_ = raster_body_window(laid, y0, y1)
    ref = full_img.crop((0, y0, full_img.width, y1))
    assert np.array_equal(np.asarray(win), np.asarray(ref))
