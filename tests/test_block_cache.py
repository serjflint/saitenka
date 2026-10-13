"""Windowed-panel compression, eviction and retained-pixel bounds."""

from __future__ import annotations

import numpy as np

from saitenka.panel import Definition, Entry, panel_rows, render_panel
from saitenka.render.banded import BandedTuning, WindowedPanel

WIDTH = 384


def _fabricated(n_defs: int) -> Entry:
    # Distinct per-def bodies so a misplaced/stale block anywhere in the sweep produces a pixel diff.
    return Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[
            Definition(
                f"辞書{i}",
                [f"意味{i}：とても長い説明文がここに続いて縦に伸びる。" * (20 if i == 0 else 2)],
            )
            for i in range(n_defs)
        ],
    )


def test_compressed_cache_round_trips_to_identical_pixels():
    entry = _fabricated(10)
    plain = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(compress=False))
    packed = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(compress=True))
    for scroll in (0, 200, 500):
        a = np.asarray(plain.viewport(scroll, 260))
        b = np.asarray(packed.viewport(scroll, 260))
        assert np.array_equal(a, b)  # packed storage is lossless


def test_lru_cap_bounds_retained_pixels_under_sustained_scroll():
    entry = _fabricated(64)  # ~a real polysemous word's block count
    reference = np.asarray(render_panel(entry, width=WIDTH), np.int16)
    total = reference.shape[0]
    cap, vh, overscan = 16, 300, 40
    wp = WindowedPanel(
        panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(max_cached_blocks=cap, compress=True)
    )
    max_scroll = total - vh
    positions = [*range(0, max_scroll, 40), max_scroll]
    peak = 0
    for scroll in positions:
        viewport = np.asarray(wp.viewport(scroll, vh, overscan=overscan), np.int16)
        assert np.array_equal(viewport, reference[scroll : scroll + vh])
        peak = max(peak, wp.cached_blocks)
    assert wp.measured == wp.count
    assert peak <= cap


def test_scrolling_back_up_stays_exact_after_lru_eviction():
    entry = _fabricated(40)
    total = render_panel(entry, width=WIDTH).height
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(max_cached_blocks=4))
    wp.viewport(total - 300, 300)  # bottom: measures everything, LRU-evicts the top's pixels
    ref = render_panel(entry, width=WIDTH).crop((0, 0, WIDTH, 300))
    win = wp.viewport(0, 300)  # back to top: evicted blocks re-render, offsets still exact
    assert np.abs(np.asarray(win, np.int16) - np.asarray(ref, np.int16)).max() == 0
