"""Stage 3 of the scale-boundary rewrite: the controller composites the ONE reference panel natively.

Behind ``[tooltip] scale_boundary`` (default off), the tooltip drops the second native panel: the blit
composites the reference panel at the display scale (`_blit_native`), `hit_target` always returns the
reference geometry, and the crisp worker doesn't build a second panel. This asserts the flag-on
behaviour — the DRAWN panel is the reference panel, the seam round-trips (structurally, one geometry),
and no `_crisp_cache` entry is minted.
"""

from __future__ import annotations

import pytest
from tip_fakes import hidpi_reader

from overlay.app import tooltip
from overlay.app.subtitle_render import NullRenderer

_SCALES = [1.5, 2.0]


def _reader(scale: float, monkeypatch):
    r = hidpi_reader(scale)
    r._scale_boundary = True  # flip the one-panel path on
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.hover = 0
    r._show_tooltip(0)
    return r


@pytest.mark.parametrize("scale", _SCALES)
def test_hit_target_is_the_one_reference_panel(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    panel, s, scroll = tooltip.hit_target(r, nested=False)
    assert panel is r._tip_state  # the ONE reference panel — not a second native panel
    assert s == r._tip_display_scale
    assert scroll == r._tip_scroll
    assert len(r._crisp_cache) == 0  # request_crisp built no second panel


@pytest.mark.parametrize("scale", _SCALES)
def test_drawn_element_round_trips_through_the_one_panel(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    panel, s, scroll = tooltip.hit_target(r, nested=False)
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
def test_blit_composited_native_not_soft(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    assert r._crisp_miss == ""  # a hi-dpi show composited crisp natively (no soft-miss reason)
    # the uploaded rect is display px: the reference panel width × the display scale
    assert r._tip_rect is not None
    assert r._tip_rect[2] == round(r.tip_width * r._tip_display_scale)


def test_navigated_view_is_keyless_and_still_round_trips(monkeypatch):
    # A link-navigated view builds no second panel (nav crisp install is a no-op under the flag); it
    # composites native from its own reference panel and the seam still holds.
    r = _reader(2.0, monkeypatch)
    tooltip.navigate_tip(r, "見る")
    assert r._tip_key is None  # no synthetic nav key needed — one panel
    assert len(r._crisp_cache) == 0
    panel, s, scroll = tooltip.hit_target(r, nested=False)
    assert panel is r._tip_state
    panel.windowed.viewport(0, 1_000_000)
    sx, sy = r._tip_xy
    for b in panel.windowed.scan_boxes():
        mx, my = sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s
        assert r._scan_hit(mx, my) == b
