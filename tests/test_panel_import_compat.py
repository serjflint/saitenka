"""Compatibility surface retained while the panel implementation moves into a package."""

from __future__ import annotations


def test_body_block_reexports_panel_workers():
    import saitenka.body_block as legacy
    import saitenka.panel.body as current

    assert legacy.BodyRenderArgs is current.BodyRenderArgs
    assert legacy.render_body_band is current.render_body_band
    assert legacy.render_body_block is current.render_body_block
