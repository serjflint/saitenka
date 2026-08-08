"""Stage 4: the windowed panel as a bounded block cache — exhaustive ``windowed == crop(full)`` parity
at EVERY offset, a compressed-cache round trip, and a bounded retained-pixel set under sustained scroll.

Parity is the whole game for the eventual swap (Stage 7), so this sweeps all offsets 1px at a time on
a fabricated 64-block entry rather than sampling — any single stale/misplaced block anywhere shows."""

from __future__ import annotations

import numpy as np
import pytest
from overlay.panel import Definition, Entry, panel_rows, render_panel
from overlay.render.banded import CachedBlock, WindowedPanel

WIDTH = 384


def _fabricated(n_defs: int) -> Entry:
    # Distinct per-def bodies so a misplaced/stale block anywhere in the sweep produces a pixel diff.
    return Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[
            Definition(f"辞書{i}", [f"意味{i}：とても長い説明文がここに続いて縦に伸びる。" * 2])
            for i in range(n_defs)
        ],
    )


@pytest.mark.parametrize("compress", [False, True])
def test_windowed_equals_full_crop_at_every_offset(compress):
    entry = _fabricated(64)
    ref = render_panel(entry, width=WIDTH)
    total, vh = ref.height, 300
    ref_arr = np.asarray(ref, np.int16)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, max_cached_blocks=8, compress=compress)
    for scroll in range(total - vh + 1):  # EVERY offset, 1px steps
        win = np.asarray(wp.viewport(scroll, vh, overscan=60), np.int16)
        assert np.abs(win - ref_arr[scroll : scroll + vh]).max() == 0, (
            f"mismatch at scroll={scroll}"
        )


def test_compressed_cache_round_trips_to_identical_pixels():
    entry = _fabricated(10)
    plain = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, compress=False)
    packed = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, compress=True)
    for scroll in (0, 200, 500):
        a = np.asarray(plain.viewport(scroll, 260))
        b = np.asarray(packed.viewport(scroll, 260))
        assert np.array_equal(a, b)  # zlib pack/unpack of a block is lossless


def test_cached_block_compress_is_lossless():
    from PIL import Image

    img = render_panel(_fabricated(2), width=WIDTH)
    live = CachedBlock.make(16, 0, img, [], [], compress=False)
    packed = CachedBlock.make(16, 0, img, [], [], compress=True)
    assert np.array_equal(np.asarray(live.image()), np.asarray(packed.image().convert("RGBA")))
    assert isinstance(packed.image(), Image.Image)


def test_lru_cap_bounds_retained_pixels_under_sustained_scroll():
    entry = _fabricated(64)  # ~a real polysemous word's block count
    total = render_panel(entry, width=WIDTH).height
    cap, vh, overscan = 6, 300, 40
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, max_cached_blocks=cap, compress=True)
    peak = 0
    for scroll in range(0, total - vh, 40):
        wp.viewport(scroll, vh, overscan=overscan)
        peak = max(peak, wp.cached_blocks)
    assert wp.measured == wp.count  # every block measured over the full scroll (offsets all exact)
    # The cap always protects the visible window, so peak can exceed cap only by the widest visible
    # span. Compute that span exactly from the (now fully-measured) offset table — never the full count.
    table = wp._offsets.exact_table()
    max_span = max(
        len(range(*table.visible_range(s, vh, overscan))) for s in range(0, total - vh, 40)
    )
    assert peak <= max(cap, max_span)
    assert max_span < wp.count  # sanity: the window really is a small slice of the 64-block panel


def test_scrolling_back_up_stays_exact_after_lru_eviction():
    entry = _fabricated(40)
    total = render_panel(entry, width=WIDTH).height
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, max_cached_blocks=4)
    wp.viewport(total - 300, 300)  # bottom: measures everything, LRU-evicts the top's pixels
    ref = render_panel(entry, width=WIDTH).crop((0, 0, WIDTH, 300))
    win = wp.viewport(0, 300)  # back to top: evicted blocks re-render, offsets still exact
    assert np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16)).max() == 0
