"""The controller display↔hit-test SEAM — the picking-buffer agreement oracle at the tooltip level.

The regression class this guards: the panel DRAWN (crisp native at hi-dpi, else the soft reference) must
be the panel HIT-TESTED, or hover/click land on the wrong element. Two independent failures already shipped
here — the native panel's wrap diverging from the reference's while the reference was hit-tested, and a whole
view class (link-navigated) that couldn't composite crisp because it was keyless. Both are the same oracle:
a point at any drawn element's DISPLAYED centre must round-trip through the controller's hit path back to
that element, at every scale × view. This asserts the invariant, not any current geometry — no golden, so it
fails (rather than canonizes) if the seam drifts again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import util
from tip_fakes import hidpi_reader as _seam_reader

from overlay.app import tooltip
from overlay.app.subtitle_render import NullRenderer
from overlay.model import Theme
from overlay.panel import render_panel

if TYPE_CHECKING:
    from overlay.app.controller import Reader

# osd_h / REF_H(1080) == _tip_display_scale, so these osds pin the crisp path on (scale > 1.05).
_SCALES = [1.5, 2.0]


def _measure_all(panel) -> None:
    panel.windowed.viewport(0, 1_000_000)  # force every block measured → full scan/link geometry


def _install_native(r: Reader, key, tok) -> None:
    """Build the word's native-scale panel synchronously and cache it (no crisp worker), so the drawn
    panel is the crisp native one — the divergent geometry the seam has to agree on."""
    scale = r._tip_display_scale
    panel = tooltip.build_native_panel(
        r, tok, tok.surface, key, r._tip_cap(), scale, mined=False, anki=False
    )
    tooltip._crisp_store(r, key, scale, panel)


def _round_trip_scan(r: Reader, panel, s: float, scroll: int, xy) -> None:
    sx, sy = xy
    for b in panel.windowed.scan_boxes():
        mx = (
            sx + (b.x + b.w / 2) * s
        )  # panel-space cell centre → its DISPLAYED point (invert hit map)
        my = sy + (b.y + b.h / 2 - scroll) * s
        assert (
            r._scan_hit(mx, my) == b
        )  # …and the controller hit path maps it back to that same cell


def _round_trip_links(panel, s: float, scroll: int, xy, link_hit) -> None:
    sx, sy = xy
    boxes = panel.windowed.link_boxes()
    assert boxes  # the entry really has clickable links — the test isn't vacuously passing
    for lb in boxes:
        mx = sx + (lb.x + lb.w / 2) * s
        my = sy + (lb.y + lb.h / 2 - scroll) * s
        assert link_hit(mx, my) == lb


@pytest.mark.parametrize("scale", _SCALES)
def test_hovered_view_drawn_element_round_trips_through_hit_target(scale, monkeypatch):
    r = _seam_reader(scale)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._crisp_on = False  # show soft first (no worker), then install the native panel by hand
    r.hover = 0
    r._show_tooltip(0)
    r._crisp_on = True
    _install_native(r, r._tip_key, r.tokens[0])

    panel, s, scroll = tooltip.hit_target(r, nested=False)
    assert panel.width == round(
        r.tip_width * scale
    )  # the DRAWN panel is the native one, not reference
    assert panel is tooltip.crisp_lookup(
        r, r._tip_key
    )  # …and it's exactly what the blit path composites
    _measure_all(panel)
    _round_trip_scan(r, panel, s, scroll, r._tip_xy)
    _round_trip_links(panel, s, scroll, r._tip_xy, r._tip_link_hit)


@pytest.mark.parametrize("scale", _SCALES)
def test_navigated_view_drawn_element_round_trips_through_hit_target(scale, monkeypatch):
    # The link-navigated view (was permanently soft + keyless → hit-tested the wrong panel). It keys to
    # its query and composites crisp like a hovered word; the seam must hold for it too.
    r = _seam_reader(scale)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r.hover = 0
    r._show_tooltip(0)
    tooltip.navigate_tip(r, "見る")  # click a cross-reference → in-place navigation

    assert r._tip_key is not None  # keyed (a keyless view can't find its drawn panel)
    panel, s, scroll = tooltip.hit_target(r, nested=False)
    assert panel.width == round(r.tip_width * scale)
    assert panel is tooltip.crisp_lookup(r, r._tip_key)
    _measure_all(panel)
    _round_trip_scan(r, panel, s, scroll, r._tip_xy)
    _round_trip_links(panel, s, scroll, r._tip_xy, r._tip_link_hit)


@pytest.mark.parametrize("scale", _SCALES)
def test_nested_view_drawn_link_round_trips_through_hit_target(scale, monkeypatch):
    r = _seam_reader(scale)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    tok = r.tokens[0]
    key = r._panel_key(tok, tok.surface, mined=False)
    st = r._panel_for(tok, tok.surface, min_h=r._tip_cap(), mined=False)
    r._nest.state, r._nest.key, r._nest.token, r._nest.word = st, key, tok, tok.surface
    r._nest.view_h, r._nest.scroll, r._nest.xy = min(st.full_height, r._tip_cap()), 0, (10, 10)
    _install_native(r, key, tok)

    panel, s, scroll = tooltip.hit_target(r, nested=True)
    assert panel.width == round(r.tip_width * scale)  # nested draws the crisp native panel too
    assert panel is tooltip.crisp_lookup(r, r._nest.key)
    _measure_all(panel)
    _round_trip_links(panel, s, scroll, r._nest.xy, r._nest_link_hit)


@pytest.mark.parametrize("scale", _SCALES)
def test_soft_fallback_view_round_trips_through_hit_target(scale, monkeypatch):
    # When no native panel is built (crisp not yet ready / disabled), the DRAWN panel is the soft
    # reference upscaled by the display scale — the hit path must invert exactly that upscale.
    r = _seam_reader(scale)
    monkeypatch.setattr(r, "renderer", NullRenderer())
    r._crisp_on = False
    r.hover = 0
    r._show_tooltip(0)

    panel, s, scroll = tooltip.hit_target(r, nested=False)
    assert panel is r._tip_state and s == r._tip_display_scale  # reference panel at display scale
    _measure_all(panel)
    _round_trip_scan(r, panel, s, scroll, r._tip_xy)
    _round_trip_links(panel, s, scroll, r._tip_xy, r._tip_link_hit)


@pytest.mark.parametrize("scale", [1.5, 1.76, 2.0])
def test_native_panel_is_a_faithful_scaled_render_of_the_reference(scale):
    # Regression for the vertical-geometry bug: the crisp native panel (Theme(scale), width×scale) must be
    # a faithful scaled render of the reference — its full height byte-equals a one-shot render_panel at
    # that scale. Before the fix the windowed engine took scale-1.0 top-margin + inter-row gaps (the theme
    # wasn't forwarded), so the native panel was ~200px shorter than 2× the reference → crisp/soft drifted
    # apart as you scrolled, and y0 = scroll×scale over-ran the (too short) native panel.
    r = _seam_reader(scale)
    tok = r.tokens[0]
    key = r._panel_key(tok, tok.surface, mined=False)
    native = tooltip.build_native_panel(
        r, tok, tok.surface, key, r._tip_cap(), scale, mined=False, anki=False
    )
    _measure_all(native)
    native_w = round(r.tip_width * scale)
    ref = render_panel(util.cjk_links_entry(4), width=native_w, theme=Theme(scale=scale))
    assert native.full_height == ref.height
