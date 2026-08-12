"""Stage 1: property tests for the pure block-geometry core (``saitenka.render.window``).

The offset table + half-open visible-range kernel + partial/estimated table are the math the windowed
tooltip engine is grown from, so every invariant is pinned here before a pixel is rendered. Strategies
bound heights to >= 1 (real blocks are never zero-height) and counts/gaps to realistic tooltip ranges.
Shrunk counterexamples from ``poe mutate window`` are pinned as ``@example`` (repo convention)."""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from hypothesis import example, given
from hypothesis import strategies as st

from saitenka.render.window import LazyOffsets, OffsetTable, build_offsets

# Realistic tooltip geometry: a handful to ~64 blocks, per-block heights of real rows, small gaps.
heights_st = st.lists(st.integers(min_value=1, max_value=400), min_size=1, max_size=64)
gaps_st = st.integers(min_value=0, max_value=12)
pad_st = st.integers(min_value=0, max_value=40)


@st.composite
def tables(draw) -> OffsetTable:
    heights = draw(heights_st)
    gaps = [draw(gaps_st) for _ in heights]
    return build_offsets(heights, gaps, draw(pad_st), draw(pad_st))


# --- offset-table invariants ---------------------------------------------------------------------


@given(tables())
def test_starts_and_ends_are_non_decreasing_and_end_is_start_plus_height(t: OffsetTable):
    for i in range(t.count):
        assert t.ends[i] >= t.starts[i]  # non-negative height
        if i:
            assert t.starts[i] >= t.ends[i - 1]  # blocks never overlap (a gap sits between)
            assert t.starts[i] > t.starts[i - 1]  # strictly increasing (heights >= 1)


@given(tables())
def test_total_spans_top_margin_blocks_and_bottom_margin(t: OffsetTable):
    assert t.starts[0] == t.top_pad  # first block sits below exactly the top margin
    assert t.total == t.ends[-1] + t.bottom_pad  # bottom margin below the last block


@given(heights_st, gaps_st, pad_st, pad_st)
def test_total_matches_compose_panel_arithmetic(heights, gap, top, bottom):
    # compose_panel: 2*margin + top_reserve + Σheights + Σ(n-1 inter-gaps); here top_pad folds
    # margin+top_reserve and bottom_pad is the bottom margin, uniform gap between blocks.
    gaps = [gap] * len(heights)
    t = build_offsets(heights, gaps, top, bottom)
    n = len(heights)
    expected = top + bottom + sum(heights) + gap * (n - 1)
    assert t.total == expected


# --- visible-range kernel ------------------------------------------------------------------------


@given(tables(), st.integers(0, 5000), st.integers(1, 2000), st.integers(0, 400))
@example(build_offsets([1], [0], 0, 0), 0, 1, 0)  # single block, exactly covered
def test_visible_range_is_exactly_the_intersecting_blocks(t, scroll, vh, overscan):
    start, end = t.visible_range(scroll, vh, overscan)
    lo, hi = scroll - overscan, scroll + vh + overscan
    for i in range(t.count):
        visible = t.starts[i] < hi and t.ends[i] > lo  # half-open discipline
        assert (start <= i < end) == visible  # completeness AND no false positives


@given(tables(), st.integers(0, 5000), st.integers(1, 2000), st.integers(0, 400))
def test_visible_range_is_well_formed_and_clamped(t, scroll, vh, overscan):
    start, end = t.visible_range(scroll, vh, overscan)
    assert 0 <= start <= end <= t.count


@given(
    tables(), st.integers(0, 4000), st.integers(1, 100), st.integers(1, 2000), st.integers(0, 200)
)
def test_scrolling_down_never_decreases_the_visible_indices(t, scroll, step, vh, overscan):
    a_start, a_end = t.visible_range(scroll, vh, overscan)
    b_start, b_end = t.visible_range(scroll + step, vh, overscan)
    assert b_start >= a_start  # monotonic: content scrolling up can only advance the window
    assert b_end >= a_end


@given(tables(), st.integers(0, 5000), st.integers(1, 2000))
def test_overscan_only_grows_the_window(t, scroll, vh):
    tight = set(range(*t.visible_range(scroll, vh, 0)))
    loose = set(range(*t.visible_range(scroll, vh, 200)))
    assert tight <= loose  # overscan never drops a block that was visible without it


# --- content_y <-> (block, local_y) round trip ---------------------------------------------------


@given(tables(), st.integers(0, 8000))
def test_block_at_agrees_with_a_linear_scan(t: OffsetTable, y: int):
    expected = next((i for i in range(t.count) if t.starts[i] <= y < t.ends[i]), None)
    assert t.block_at(y) == expected


@given(tables(), st.integers(0, 8000))
def test_content_y_local_y_round_trip_inside_blocks(t: OffsetTable, y: int):
    got = t.local_y(y)
    if got is None:
        assert t.block_at(y) is None  # y is in a gap or a margin
    else:
        block, local = got
        assert 0 <= local < t.ends[block] - t.starts[block]
        assert t.content_y(block, local) == y  # exact inverse


# --- LazyOffsets: partially-known table ----------------------------------------------------------


@st.composite
def lazy_and_heights(draw):
    heights = draw(heights_st)
    gaps = [draw(gaps_st) for _ in heights]
    lazy = LazyOffsets(gaps, draw(pad_st), draw(pad_st), seed_height=draw(st.integers(0, 200)))
    return lazy, heights


@given(lazy_and_heights())
def test_prefix_offsets_are_exact_and_stable_as_heights_arrive(pair):
    lazy, heights = pair
    exact_starts = build_offsets(heights, lazy._gaps, lazy._top, lazy._bottom).starts
    # Feed heights top-down; after each, every measured prefix block reports its final exact start.
    for k, h in enumerate(heights):
        lazy.set_height(k, h)
        assert lazy.prefix_len == k + 1
        for i in range(k + 1):
            assert lazy.start_exact(i)
            assert lazy.start(i) == exact_starts[i]  # never moves as later blocks are measured


@given(lazy_and_heights())
def test_estimate_converges_to_exact_when_all_measured(pair):
    lazy, heights = pair
    for k, h in enumerate(heights):
        lazy.set_height(k, h)
    exact = build_offsets(heights, lazy._gaps, lazy._top, lazy._bottom)
    assert lazy.total_estimate() == exact.total
    assert lazy.estimated_table().starts == exact.starts
    assert lazy.exact_table() == exact


@given(lazy_and_heights())
def test_offset_below_the_measured_prefix_is_flagged_inexact(pair):
    lazy, heights = pair
    if len(heights) < 2:
        return
    lazy.set_height(1, heights[1])  # measure a middle block, leave block 0 unknown
    assert lazy.prefix_len == 0
    assert not lazy.start_exact(1)  # start(1) depends on the unmeasured block 0 → estimate


@given(
    gaps=st.lists(gaps_st, min_size=1, max_size=20),
    top=pad_st,
    bottom=pad_st,
    seed=st.integers(0, 300),
)
def test_estimated_table_matches_visible_range_binary_search(gaps, top, bottom, seed):
    # With no heights measured the estimated table is uniform (all seed) — its visible_range must still
    # match a brute-force scan, proving estimate/exact share one kernel.
    lazy = LazyOffsets(gaps, top, bottom, seed_height=seed)
    t = lazy.estimated_table()
    start, end = t.visible_range(100, 300, 0)
    lo, hi = 100, 400
    assert start == bisect_right(t.ends, lo)
    assert end == max(start, bisect_left(t.starts, hi))


def test_read_reflects_a_height_set_after_a_prior_read():
    # A read caches the offset table; a later set_height must invalidate it, so the next read is not stale.
    lazy = LazyOffsets([0, 0], top_pad=10, bottom_pad=5, seed_height=100)
    before = lazy.start(1)  # both unknown → estimated from the seed
    lazy.set_height(0, 30)
    assert lazy.start(1) != before
    assert lazy.start(1) == 10 + 30  # top_pad + block 0's measured height (gap 0)
