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

from saitenka.app.features.tooltip import tooltip, tooltip_panel
from saitenka.app.subtitle_render import NullRenderer

#: Every scale outside the soft band, both sides of it: the native tier is not a hi-dpi feature.
_SCALES = [2 / 3, 1.5, 2.0]


def _reader(scale: float, monkeypatch):
    r = hidpi_reader(scale).graph
    monkeypatch.setattr(r.subtitle_presentation, "renderer", NullRenderer())
    r.tooltip.select(0)
    r.tooltip.show_tooltip(0)
    return r


@pytest.mark.parametrize("scale", _SCALES)
def test_hit_target_is_the_one_reference_panel(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    panel, s, scroll = tooltip_panel.hit_target(
        r.tooltip.surface_state().nest,
        r.tooltip.surface_state().view.state,
        r.tooltip.surface_state().view.scroll,
        r.tooltip.scale().raster,
        nested=False,
    )
    assert (
        panel is r.tooltip.surface_state().view.state
    )  # the ONE reference panel — there is no second native panel
    assert s == r.tooltip.scale().raster  # inverse == the (bucketed) scale the blit drew at
    assert scroll == r.tooltip.surface_state().view.scroll


@pytest.mark.parametrize("scale", _SCALES)
def test_drawn_element_round_trips_through_the_one_panel(scale, monkeypatch):
    r = _reader(scale, monkeypatch)
    panel, s, scroll = tooltip_panel.hit_target(
        r.tooltip.surface_state().nest,
        r.tooltip.surface_state().view.state,
        r.tooltip.surface_state().view.scroll,
        r.tooltip.scale().raster,
        nested=False,
    )
    panel.windowed.viewport(0, 1_000_000)  # force every block measured → full geometry
    sx, sy = r.tooltip.surface_state().view.xy
    scans = panel.windowed.scan_boxes()
    links = panel.windowed.link_boxes()
    assert scans and links
    for b in scans:
        mx, my = sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s
        assert (
            tooltip_panel.scan_hit(r.tooltip.surface_state(), r.tooltip.scale().raster, mx, my) == b
        )
    for lb in links:
        mx, my = sx + (lb.x + lb.w / 2) * s, sy + (lb.y + lb.h / 2 - scroll) * s
        assert (
            tooltip_panel.link_hit_at(
                r.tooltip.surface_state(),
                r.tooltip.scale().raster,
                mx,
                my,
                nested=False,
            )
            == lb
        )


@pytest.mark.parametrize("scale", _SCALES)
def test_cold_paint_is_soft_then_upgrades_to_crisp_when_bands_warm(scale, monkeypatch):
    # Soft-first (plan B3): a cold hi-dpi show paints SOFT instantly (the main thread never rasters
    # native), flags a pending upgrade, and the poll loop swaps to crisp once a worker warms the bands.
    r = _reader(scale, monkeypatch)  # no worker in the test → the show paints soft
    assert (
        r.tooltip.surface_state().view.crisp_miss == "warming"
        and r.tooltip.surface_state().view.crisp_pending
    )  # cold → soft, upgrade pending

    st = r.tooltip.surface_state().view.state
    vh = min(r.tooltip.surface_state().view.view_h, st.full_height)
    y0 = max(0, min(r.tooltip.surface_state().view.scroll, max(0, st.full_height - vh)))
    st.viewport(
        y0, vh, scale=r.tooltip.scale().raster
    )  # simulate the worker warming the native viewport
    tooltip_panel.apply_pending_crisp(
        r.tooltip.tip_ports, r.tooltip.surface_state().view
    )  # the poll-loop upgrade

    assert (
        r.tooltip.surface_state().view.crisp_miss == ""
        and not r.tooltip.surface_state().view.crisp_pending
    )  # now composited crisp
    assert r.tooltip.surface_state().view.rect is not None
    assert r.tooltip.surface_state().view.rect[2] == round(
        r.tooltip.scale().width * r.tooltip.scale().raster
    )  # native display width


@pytest.mark.parametrize("scale", _SCALES)
def test_warm_native_viewport_composites_crisp_immediately(scale, monkeypatch):
    # When the bands are already warm (worker ran ahead), the show composites crisp on the first paint.
    r = hidpi_reader(scale).graph
    monkeypatch.setattr(r.subtitle_presentation, "renderer", NullRenderer())
    r.tooltip.select(0)
    r.tooltip.show_tooltip(0)  # first paint (soft) also measures the panel
    st = r.tooltip.surface_state().view.state
    vh = min(r.tooltip.surface_state().view.view_h, st.full_height)
    y0 = max(0, min(r.tooltip.surface_state().view.scroll, max(0, st.full_height - vh)))
    st.viewport(y0, vh, scale=r.tooltip.scale().raster)  # warm the native viewport
    tooltip_panel.render_view(
        r.tooltip.tip_ports, r.tooltip.surface_state().view
    )  # re-blit with warm bands
    assert (
        r.tooltip.surface_state().view.crisp_miss == ""
        and not r.tooltip.surface_state().view.crisp_pending
    )


@pytest.mark.parametrize("scale", _SCALES)
def test_the_native_scrollbar_thumb_is_sized_in_display_px(scale, monkeypatch):
    """`_blit_native` hands `decorate_and_upload` `round(y0*scale), round(full_h*scale)` — the thumb
    is drawn onto an already-display-sized array, so its inputs must be display px too.

    Nothing watched this line. Passing the unscaled `y0, full_h` instead leaves the whole suite
    green while every native frame draws a thumb sized against the wrong denominator — 36% short at
    0.65. The thumb's POSITION survives that bug (the `y0/(full_h-vh)` ratio is scale-invariant),
    which is why asserting where it sits would not have caught it either. Its height is the tell.
    """
    r = hidpi_reader(scale).graph
    monkeypatch.setattr(r.subtitle_presentation, "renderer", NullRenderer())
    uploaded: list = []
    monkeypatch.setattr(
        r.overlay, "show_bgra", lambda bgra, *_a, **_k: uploaded.append(bgra.copy())
    )
    r.tooltip.select(0)
    r.tooltip.show_tooltip(0)
    view = r.tooltip.surface_state().view
    st = view.state
    vh = min(view.view_h, st.full_height)
    y0 = max(0, min(view.scroll, max(0, st.full_height - vh)))
    st.viewport(y0, vh, scale=r.tooltip.scale().raster)  # warm the native viewport
    # `full_height` is a converging estimate and the blit itself advances it (664 -> 684 at 2.0), so
    # the denominator has to be read BEFORE, not after — reading it after silently understates it.
    reference_full_h = st.full_height
    tooltip_panel.render_view(r.tooltip.tip_ports, view)
    assert view.crisp_miss == ""  # the native path drew this one

    frame = uploaded[-1]
    height, width = frame.shape[:2]
    display_full_h = round(reference_full_h * r.tooltip.scale().raster)
    assert display_full_h > height  # there is something to scroll, so there is a thumb
    thumb = frame[:, width - 7 : width - 3]  # the track `decorate_and_upload` paints into
    drawn = int((thumb == (99, 99, 99, 210)).all(axis=-1).any(axis=1).sum())

    assert drawn == max(28, int((height - 8) * height / display_full_h))


def test_a_sub_1080p_tooltip_uploads_display_sized_pixels(monkeypatch):
    """A cold sub-1080p show still composites at the 1920x1080 reference and resizes down at upload —
    and the pixels that reach mpv, not just ``rect``, have to be that size.

    This is the soft FALLBACK, the one frame before the native bands warm; the band makes the steady
    state native, so the resize is no longer paid per notch. It is still the only place one is paid
    at all, and nothing watched it: dropping it outright left the whole suite green while every
    sub-1080p tooltip uploaded a frame half again too big for its OSD, with `rect` still claiming
    the smaller one the hit-test inverts.
    """
    r = hidpi_reader(2 / 3).graph
    monkeypatch.setattr(r.subtitle_presentation, "renderer", NullRenderer())
    uploaded: list[tuple[int, ...]] = []

    def record(bgra, *_args, **_kwargs):
        uploaded.append(bgra.shape)
        return {"error": "success"}

    monkeypatch.setattr(r.overlay, "show_bgra", record)
    r.tooltip.select(0)
    r.tooltip.show_tooltip(0)

    scale = r.tooltip.scale()
    assert not tooltip_panel.soft_scale(scale.raster)  # outside the band: the native tier's range
    assert (
        r.tooltip.surface_state().view.crisp_miss == "warming"
    )  # but cold, so this IS the soft one
    height, width = uploaded[-1][:2]
    assert width == round(scale.width * scale.display)  # the reference panel, resized down
    assert (width, height) == r.tooltip.surface_state().view.rect[2:4]  # what the hit-test inverts


def test_navigated_view_is_keyless_and_still_round_trips(monkeypatch):
    # A link-navigated view builds no second panel — it composites native from its own reference panel
    # and the seam still holds, with no synthetic key.
    r = _reader(2.0, monkeypatch)
    tooltip.navigate_tip(r.tooltip.tip_ports, r.tooltip.panel_ports, "見る")
    assert r.tooltip.surface_state().view.key is None  # no synthetic nav key needed — one panel
    panel, s, scroll = tooltip_panel.hit_target(
        r.tooltip.surface_state().nest,
        r.tooltip.surface_state().view.state,
        r.tooltip.surface_state().view.scroll,
        r.tooltip.scale().raster,
        nested=False,
    )
    assert panel is r.tooltip.surface_state().view.state
    panel.windowed.viewport(0, 1_000_000)
    sx, sy = r.tooltip.surface_state().view.xy
    for b in panel.windowed.scan_boxes():
        mx, my = sx + (b.x + b.w / 2) * s, sy + (b.y + b.h / 2 - scroll) * s
        assert (
            tooltip_panel.scan_hit(r.tooltip.surface_state(), r.tooltip.scale().raster, mx, my) == b
        )
