"""Cache equivalence: a cached render is byte-identical to a cold one.

The later caches (per-band BGRA memo, the native-crisp compose, the persistent render cache) are pure
accelerators — they must never change a pixel. The existing suite proves they're *non-load-bearing*
(opt-out still renders); this proves the stronger contract they were added for: WARM output == COLD
output, byte-for-byte, plus the same hit geometry. Single-threaded on purpose — concurrency is out of
scope (multiprocessing / 3.13 fallback), so this is pure determinism, not a race probe.

Scope: the two in-memory caches (recompute equivalence, the strongest form — a genuine re-raster) plus
the on-disk render cache (a lossless serialize→reload round-trip). The glyph mask atlas is a fonts-layer
round-trip (masks in → identical masks out) and is left to the fonts tests, not duplicated here.
"""

from __future__ import annotations

import numpy as np
import pytest
import util

from saitenka.model import Theme
from saitenka.panel import panel_rows, render_panel
from saitenka.render.banded import BandedTuning, WindowedPanel


def _bgra_band_case():
    """Per-band BGRA memo: a warm panel (retains bands + their converted BGRA) vs a cold one capped to
    one block (nothing retained → every band re-rasterised) must composite the same viewport."""
    theme, width, entry = Theme(), 640, util.tall_entry(8)
    total = render_panel(entry, width=width, theme=theme).height
    scroll, vh = total // 3, 240
    warm = WindowedPanel(panel_rows(entry, width, theme), width, theme)
    warm.viewport_bgra(0, vh)  # scroll through so the target bands are cached + memoised…
    warm.viewport_bgra(max(0, scroll - 60), vh)
    a = warm.viewport_bgra(scroll, vh)  # served from the warm memo
    cold = WindowedPanel(
        panel_rows(entry, width, theme), width, theme, tuning=BandedTuning(max_cached_blocks=1)
    )
    b = cold.viewport_bgra(scroll, vh)  # cap=1 → the same bands rastered fresh this call
    warm.viewport(0, total)
    cold.viewport(0, total)
    return a, b, warm.scan_boxes(), cold.scan_boxes()


def _native_crisp_case():
    """One-panel native compose: compositing the reference panel at the display scale a second time (its
    native bands now warm) vs a fresh panel (cold bands) must yield the same native-scale BGRA."""
    theme, width, entry, scale = Theme(), 640, util.cjk_links_entry(6), 2.0
    total = render_panel(entry, width=width, theme=theme).height
    vh = 240
    warm = WindowedPanel(panel_rows(entry, width, theme), width, theme)
    for s in range(0, max(1, total - vh) + 1, 40):  # measure the whole panel (offset table)
        warm.viewport(s, vh)
    warm.viewport_bgra(0, vh, scale=scale)  # warm the native bands…
    a = warm.viewport_bgra(0, vh, scale=scale)  # …served from the warm native cache
    cold = WindowedPanel(panel_rows(entry, width, theme), width, theme)
    b = cold.viewport_bgra(0, vh, scale=scale)  # native bands rastered fresh this call
    warm.viewport(0, total)
    cold.viewport(0, total)
    return a, b, warm.scan_boxes(), cold.scan_boxes()


@pytest.mark.parametrize(
    "case", [_bgra_band_case, _native_crisp_case], ids=["bgra_band", "native_crisp"]
)
def test_warm_output_is_byte_identical_to_cold(case):
    warm_bgra, cold_bgra, warm_boxes, cold_boxes = case()
    assert np.array_equal(
        warm_bgra, cold_bgra
    )  # not a pixel differs between cached and re-rendered
    assert warm_boxes == cold_boxes  # …and the hit geometry is identical too


def test_render_cache_roundtrip_is_lossless(tmp_path):
    # The persistent render cache stores a first-viewport BGRA (zlib) keyed by config+content. A HIT must
    # reproduce the live pixels exactly — a lossy serialize (dtype/reshape/compression bug) would paint a
    # corrupt cold-start viewport. Round-trip: put a live viewport, read it back, assert byte-identical.
    from saitenka.app.render_cache import RenderCache

    theme, width, entry = Theme(), 640, util.tall_entry(8)
    wp = WindowedPanel(panel_rows(entry, width, theme), width, theme)
    view_h, overscan = 240, 240
    live = wp.viewport_bgra(0, view_h)
    wp.viewport(0, render_panel(entry, width=width, theme=theme).height)  # settle full_height

    rc = RenderCache.open(tmp_path / "render-cache.sqlite", max_bytes=1 << 20)
    assert rc is not None
    rc.put("cfg-sig", "content-key", view_h, overscan, wp.full_height, live)
    got = rc.get("cfg-sig", "content-key", view_h, overscan)
    rc.close()
    assert got is not None  # a matching-geometry hit
    assert np.array_equal(
        got.array, live
    )  # reloaded pixels are byte-identical to the live viewport
    assert got.full_h == wp.full_height  # …and the placement height survives the round-trip
