"""Block-cache telemetry + the render-budget invariants it measures.

The per-block pixel cache (``WindowedPanel._blocks``) is the layer under the per-word ``panel_cache``
where scroll jank lives: a cold reach rasterises a whole def block. These tests pin what the counters
record and the two budgets they exist to guard — how much is rasterised on *show* (the first paint)
and per *scroll* notch. Synthetic fixed-height blocks make the pixel budget exact and font-independent.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Iterator

from overlay import otel_metrics
from overlay.panel import Row
from overlay.render.banded import _BAND_PX, WindowedPanel

WIDTH = 384
BLOCK_H = 100  # every synthetic block is this tall → rendered_px == BLOCK_H * blocks rendered


def _fixed_panel(n_blocks: int, **kw) -> WindowedPanel:
    """A panel of ``n_blocks`` uniform ``BLOCK_H``-px blocks — no fonts, no content walk, so every
    height and every render budget below is exact."""
    rows = [
        Row(0, lambda _h=BLOCK_H: (Image.new("RGBA", (WIDTH, _h), (0, 0, 0, 0)), [], []))
        for _ in range(n_blocks)
    ]
    return WindowedPanel(rows, WIDTH, **kw)


def _tall_band_row(h: int) -> Row:
    """A synthetic BANDED body row of height ``h``: ``measure`` yields the height with no raster;
    ``render_window`` rasters just the requested band. No fonts — so the per-band budget is exact."""

    def full():
        return Image.new("RGBA", (WIDTH, h), (0, 0, 0, 0)), [], []

    def measure() -> int:
        return h

    def window(y0: int, y1: int):
        return Image.new("RGBA", (WIDTH, y1 - y0), (0, 0, 0, 0)), [], []

    return Row(0, full, measure=measure, render_window=window)


def _banded_panel(heights: list[int], **kw) -> WindowedPanel:
    return WindowedPanel([_tall_band_row(h) for h in heights], WIDTH, **kw)


@contextlib.contextmanager
def _telemetry() -> Iterator[None]:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.register(reader, provider.get_meter("test"))
    try:
        yield
    finally:
        otel_metrics.unregister()
        provider.shutdown()


def _val(snap, name: str) -> float:
    return snap.get(name, {}).get("value", 0)


def test_block_cache_counters_reach_the_ctf_trace_export():
    # Regression: the block-cache counters are registered in otel_metrics but the CTF trace exporter
    # (telemetry._sample_counters) used to filter by a hand-maintained name allowlist that omitted
    # them — so a correctly-running banded render showed NO block_cache series in any report. The
    # export now derives counters by TYPE (scalar value), so any registered counter graphs; assert the
    # block-cache misses actually reach the sampled dict, and the rendered_px HISTOGRAM does not (it's
    # shown as spans, not a value-over-time track).
    from overlay.app import telemetry

    with _telemetry():
        _fixed_panel(20).viewport(0, 300)
        sampled = telemetry._sample_counters()
    assert sampled.get("block_cache.misses", 0) > 0  # the counter now reaches the trace export
    assert "block_cache.rendered_px" not in sampled  # histogram stays a span, not a counter track


def test_cold_viewport_records_a_miss_and_the_rasterised_px_per_block():
    with _telemetry():
        wp = _fixed_panel(20)
        wp.viewport(0, 300)
        snap = otel_metrics.snapshot()
    misses = _val(snap, "saitenka.block_cache.misses")
    px = snap["saitenka.block_cache.rendered_px"]
    assert misses > 0
    assert px["count"] == misses  # one render == one miss
    assert px["sum"] == misses * BLOCK_H  # exact: every block is BLOCK_H tall


def test_recompositing_the_same_viewport_records_hits_not_new_renders():
    with _telemetry():
        wp = _fixed_panel(20)
        wp.viewport(0, 300)
        first = otel_metrics.snapshot()
        wp.viewport(0, 300)  # same region → blocks already cached
        second = otel_metrics.snapshot()
    assert _val(second, "saitenka.block_cache.misses") == _val(first, "saitenka.block_cache.misses")
    assert _val(second, "saitenka.block_cache.hits") > _val(first, "saitenka.block_cache.hits")


def test_scrolling_past_the_cap_records_evictions():
    with _telemetry():
        wp = _fixed_panel(40, max_cached_blocks=3)
        for scroll in range(0, 2000, 200):  # walk the viewport down the panel
            wp.viewport(scroll, 300)
        snap = otel_metrics.snapshot()
    assert _val(snap, "saitenka.block_cache.evictions") > 0
    # retention stayed bounded near the cap (a viewport straddling a boundary keeps one extra visible
    # block, which _evict never drops), not the whole 40-block panel.
    assert wp.cached_blocks <= 5


def test_show_render_budget_is_bounded_to_the_viewport_not_the_whole_panel():
    # First paint rasterises only the blocks covering the viewport — NOT the full panel. This is the
    # invariant windowed rasterisation must preserve (and tighten): show px stays O(viewport).
    with _telemetry():
        wp = _fixed_panel(40)  # full panel is ~40 * BLOCK_H = 4000px+
        view_h = 300
        wp.viewport(0, view_h)
        snap = otel_metrics.snapshot()
    rendered = snap["saitenka.block_cache.rendered_px"]["sum"]
    assert rendered < wp.full_height  # rendered the head, not the whole panel
    assert rendered <= 2 * (view_h + BLOCK_H)  # ~one viewport (+ overscan slack), not 4000px


def test_first_reach_into_a_tall_block_rasters_at_most_one_band():
    # The PR3 crux: reaching a pathologically tall block (here 34× the viewport) rasterises only the
    # BANDS overlapping the viewport, each ≤ _BAND_PX — never the whole ~14k-px block. This is what
    # bounds a cold scroll frame to ~9ms instead of ~500ms.
    with _telemetry():
        wp = _banded_panel([14000])  # one block, 34× a 432px viewport
        view_h = 300
        wp.viewport(0, view_h)
        snap = otel_metrics.snapshot()
    px = snap["saitenka.block_cache.rendered_px"]
    assert px["sum"] <= px["count"] * _BAND_PX  # every rasterised unit is ≤ one band
    # a viewport spanning [0,300) touches bands [0,256) and [256,512) → 2 bands, ~one viewport of px
    assert px["sum"] <= 2 * (view_h + _BAND_PX)  # O(viewport), NOT O(block) — tightened to _BAND_PX
    assert px["sum"] < 14000  # emphatically not the whole block


def test_scroll_notch_into_a_tall_block_rasters_at_most_one_new_band():
    # A one-notch (86px) scroll deeper into a tall block enters at most one new band.
    with _telemetry():
        wp = _banded_panel([14000])
        wp.viewport(0, 300)
        after_show = otel_metrics.snapshot()
        wp.viewport(300, 300)  # a screen deeper — crosses into new bands
        after_scroll = otel_metrics.snapshot()
    px0 = after_show["saitenka.block_cache.rendered_px"]
    px1 = after_scroll["saitenka.block_cache.rendered_px"]
    new_px = px1["sum"] - px0["sum"]
    new_bands = px1["count"] - px0["count"]
    assert new_px <= new_bands * _BAND_PX
    assert new_px <= 2 * (
        300 + _BAND_PX
    )  # a screen-deep jump renders ~one screen of bands, not more


def test_scroll_render_budget_only_renders_newly_visible_blocks():
    with _telemetry():
        wp = _fixed_panel(40)
        wp.viewport(0, 300)
        after_show = otel_metrics.snapshot()
        wp.viewport(BLOCK_H, 300)  # nudge one block down — only the entering block is new
        after_scroll = otel_metrics.snapshot()
        wp.viewport(BLOCK_H, 300)  # re-composite the same offset — nothing new
        after_repeat = otel_metrics.snapshot()

    def misses(s):
        return _val(s, "saitenka.block_cache.misses")

    step_new = misses(after_scroll) - misses(after_show)
    assert 0 < step_new <= 2  # a one-block nudge rasterises at most the entering block (+overscan)
    assert misses(after_repeat) == misses(after_scroll)  # warm re-scroll renders nothing
