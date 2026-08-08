"""Stage 5: hit-testing on the windowed model — content-space geometry kept independent of which
blocks are materialised. Round-trip, resolve-after-eviction, and parity vs today's whole-panel scan.

The geometry (scan cells + link rects) is retained per block past pixel eviction, exactly like heights,
so scrolling back over an evicted block still hovers."""

from __future__ import annotations

import pytest
import util
from hypothesis import given, settings
from hypothesis import strategies as st
from overlay.panel import Entry, LazyPanel, panel_rows, render_panel
from overlay.render.banded import BandedTuning, WindowedPanel

WIDTH = 384


def _cjk_entry(n_defs: int = 8) -> Entry:
    return util.cjk_links_entry(n_defs)  # canonical shape lives in the shared matrix (anti-drift)


def _drive_full_scroll(wp: WindowedPanel, total: int, vh: int) -> None:
    for s in range(0, max(1, total - vh) + 1, 40):
        wp.viewport(s, vh)
    wp.viewport(max(0, total - vh), vh)  # ensure the very bottom block is rendered too


def _ref_hit(boxes, px: int, py: int):
    return next((b for b in boxes if b.x <= px < b.x + b.w and b.y <= py < b.y + b.h), None)


def test_scan_and_link_geometry_matches_whole_panel_after_full_scroll():
    entry = _cjk_entry(8)
    total = render_panel(entry, width=WIDTH).height
    ref = LazyPanel(panel_rows(entry, WIDTH), WIDTH)
    ref.finish()
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(max_cached_blocks=4))
    _drive_full_scroll(wp, total, 260)
    assert wp.scan_boxes() == ref.scan_boxes  # same cells, same panel coords, same order
    assert wp.link_boxes() == ref.link_boxes


@given(px=st.integers(0, WIDTH - 1), py=st.integers(0, 4000))
@settings(max_examples=300, deadline=None)
def test_hit_parity_at_every_point(px, py):
    entry = _cjk_entry(6)
    total = render_panel(entry, width=WIDTH).height
    ref = LazyPanel(panel_rows(entry, WIDTH), WIDTH)
    ref.finish()
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(max_cached_blocks=4))
    _drive_full_scroll(wp, total, 260)
    assert wp.scan_hit(px, py) == _ref_hit(ref.scan_boxes, px, py)  # same word hit as the old path
    assert wp.link_hit(px, py) == _ref_hit(ref.link_boxes, px, py)  # same link hit


@pytest.mark.parametrize("profile", util.PROFILES, ids=[p.id for p in util.PROFILES])
def test_point_inside_a_cell_round_trips_to_that_cell(profile):
    # The picking invariant, now ACROSS the scale × width × entry matrix: a point in any drawn cell's
    # interior hit-tests back to that cell. At hi-dpi the scan geometry is built at Theme(scale)/width×
    # scale — the wrap that the display↔hit seam has to agree on, which the old scale-1.0 run never hit.
    theme, width, entry = profile.theme, profile.width, profile.entry()
    total = render_panel(entry, width=width, theme=theme).height
    wp = WindowedPanel(panel_rows(entry, width, theme), width, theme)
    _drive_full_scroll(wp, total, 260)
    boxes = wp.scan_boxes()
    assert boxes
    for b in boxes:
        cx, cy = b.x + b.w // 2, b.y + b.h // 2  # a point in the cell's interior
        assert wp.scan_hit(cx, cy) == b  # any interior point remaps to its own cell


def test_hit_resolves_even_when_the_blocks_pixels_are_evicted():
    entry = _cjk_entry(10)
    total = render_panel(entry, width=WIDTH).height
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, tuning=BandedTuning(max_cached_blocks=3))
    # scroll to the bottom: the top blocks' pixels are LRU-evicted, but their geometry is retained.
    wp.viewport(max(0, total - 260), 260)
    top_boxes = [b for b in wp.scan_boxes() if b.y < 200]
    assert top_boxes, "expected retained top-block cells"
    b = top_boxes[0]
    assert wp.cached_blocks <= 3 + 8  # pixels are bounded (cap + visible span), not the whole panel
    hit = wp.scan_hit(b.x + b.w // 2, b.y + b.h // 2)
    assert hit == b  # …and the evicted-pixel block still hovers exactly


def test_no_hit_in_a_gap_or_over_an_unrendered_block():
    entry = _cjk_entry(8)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(0, 200)  # only the head is rendered
    # a content_y far below anything rendered → geometry unknown → no hit (can't hover what isn't shown)
    assert wp.scan_hit(WIDTH // 2, 5000) is None
    assert wp.link_hit(WIDTH // 2, 5000) is None
