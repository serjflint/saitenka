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
from overlay.render.banded import WindowedPanel

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
