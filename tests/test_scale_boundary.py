"""The one-panel crisp tooltip (scale-as-boundary): the controller composites the ONE reference panel
natively and hit-tests the SAME panel.

There is no second native panel: the blit composites the reference panel at the (bucketed) display scale
(`_blit_native`), `hit_target` returns the reference geometry, and the inverse is a single
`(mx-sx)/scale + scroll` — so the DRAWN panel is the HIT-TESTED panel and the two-geometry seam bug
cannot occur. This is the acceptance oracle for the rewrite: a drawn element's displayed centre
round-trips back to that element at every scale × view.
"""

from __future__ import annotations

import pytest
from tip_fakes import hidpi_reader

from saitenka.app import tooltip, tooltip_panel
from saitenka.app.subtitle_render import NullRenderer

_SCALES = [1.5, 2.0]


def _reader(scale: float, monkeypatch):
    r = hidpi_reader(scale)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.hover = 0
    r._show_tooltip(0)
    return r


@pytest.mark.parametrize("scale", _SCALES)
def test_hit_target_is_the_one_reference_panel(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    panel, s, scroll = tooltip_panel.hit_target(
        r._nest, r._tip_state, r._tip_scroll, r._raster_scale, nested=False
    )
    assert panel is r._tip_state  # the ONE reference panel — there is no second native panel
    assert s == r._raster_scale  # inverse == the (bucketed) scale the blit drew at
    assert scroll == r._tip_scroll


@pytest.mark.parametrize("scale", _SCALES)
def test_drawn_element_round_trips_through_the_one_panel(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    panel, s, scroll = tooltip_panel.hit_target(
        r._nest, r._tip_state, r._tip_scroll, r._raster_scale, nested=False
    )
    panel.windowed.viewport(0, 1_000_000)  # force every block measured → full geometry
    sx, sy = r._tip_xy
    scans = panel.windowed.scan_boxes()
    links = panel.windowed.link_boxes()
    assert scans and links
    for b in scans:
        mx, my = sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s
        assert r._scan_hit(mx, my) == b
    for lb in links:
        mx, my = sx + (lb.x + lb.w / 2) * s, sy + (lb.y + lb.h / 2 - scroll) * s
        assert r._tip_link_hit(mx, my) == lb


@pytest.mark.parametrize("scale", _SCALES)
def test_cold_paint_is_soft_then_upgrades_to_crisp_when_bands_warm(scale, monkeypatch):
    # Soft-first (plan B3): a cold hi-dpi show paints SOFT instantly (the main thread never rasters
    # native), flags a pending upgrade, and the poll loop swaps to crisp once a worker warms the bands.
    r = _reader(scale, monkeypatch)  # no worker in the test → the show paints soft
    assert r._crisp_miss == "warming" and r._crisp_pending  # cold → soft, upgrade pending

    st = r._tip_state
    vh = min(r._tip_view_h, st.full_height)
    y0 = max(0, min(r._tip_scroll, max(0, st.full_height - vh)))
    st.viewport(y0, vh, scale=r._raster_scale)  # simulate the worker warming the native viewport
    tooltip_panel.apply_pending_crisp(r, r._tip_view)  # the poll-loop upgrade

    assert r._crisp_miss == "" and not r._crisp_pending  # now composited crisp
    assert r._tip_rect is not None
    assert r._tip_rect[2] == round(r.tip_width * r._raster_scale)  # native display width


@pytest.mark.parametrize("scale", _SCALES)
def test_warm_native_viewport_composites_crisp_immediately(scale, monkeypatch):
    # When the bands are already warm (worker ran ahead), the show composites crisp on the first paint.
    r = hidpi_reader(scale)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.hover = 0
    r._show_tooltip(0)  # first paint (soft) also measures the panel
    st = r._tip_state
    vh = min(r._tip_view_h, st.full_height)
    y0 = max(0, min(r._tip_scroll, max(0, st.full_height - vh)))
    st.viewport(y0, vh, scale=r._raster_scale)  # warm the native viewport
    tooltip_panel.render_view(r, r.tip.view)  # re-blit with warm bands
    assert r._crisp_miss == "" and not r._crisp_pending


def test_navigated_view_is_keyless_and_still_round_trips(monkeypatch):
    # A link-navigated view builds no second panel — it composites native from its own reference panel
    # and the seam still holds, with no synthetic key.
    r = _reader(2.0, monkeypatch)
    tooltip.navigate_tip(r, "見る")
    assert r._tip_key is None  # no synthetic nav key needed — one panel
    panel, s, scroll = tooltip_panel.hit_target(
        r._nest, r._tip_state, r._tip_scroll, r._raster_scale, nested=False
    )
    assert panel is r._tip_state
    panel.windowed.viewport(0, 1_000_000)
    sx, sy = r._tip_xy
    for b in panel.windowed.scan_boxes():
        mx, my = sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s
        assert r._scan_hit(mx, my) == b
