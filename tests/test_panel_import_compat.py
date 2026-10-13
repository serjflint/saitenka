"""Compatibility surface retained while the panel implementation lives in a package."""

from __future__ import annotations


def test_body_block_reexports_its_declared_panel_surface():
    import saitenka.body_block as legacy
    import saitenka.panel.body as current

    assert all(getattr(legacy, name) is getattr(current, name) for name in legacy.__all__)
    assert set(legacy.__all__) == {
        "BodyRenderArgs",
        "LaidOutBody",
        "SCNode",
        "layout_body_block",
        "raster_body_window",
        "render_body_band",
        "render_body_block",
    }
