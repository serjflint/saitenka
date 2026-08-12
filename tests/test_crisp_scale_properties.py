"""Pre-rewrite guard for the scale-as-boundary refactor (``vibe/crisp-scale-boundary-plan.md``).

The invariant the rewrite must preserve is the display↔hit SEAM: a point inside any drawn cell must
hit-test back to that cell, at *every* display scale. The example-based tests pin fixed scales — the
engine parity matrix (``test_windowed_hit``) at the four ``PROFILES`` corners, and the controller
round-trip (``test_crisp_seam``) at 1.5 / 2.0. This adds the missing axis: a **unit** property over the
scale *continuum* at the engine level (no controller/mpv, so it stays fast), so a scale the fixed
points skip can't smuggle in a seam regression when the two-panel design collapses to one.
"""

from __future__ import annotations

import util
from hypothesis import given, settings
from hypothesis import strategies as st

from saitenka.model import Theme
from saitenka.panel import panel_rows, render_panel
from saitenka.render.banded import WindowedPanel

_REF_W = 384  # reference tip width; the native panel is laid out at round(_REF_W * scale)


def _measured_panel(scale: float) -> WindowedPanel:
    """A links+CJK panel laid out at ``Theme(scale)`` / ``width×scale`` (the crisp NATIVE geometry),
    fully rastered so every scan/link box exists — the geometry the seam has to agree on."""
    entry = util.cjk_links_entry(
        3
    )  # small: links AND scan cells, cheap to fully raster per example
    width = round(_REF_W * scale)
    theme = Theme(scale=scale)
    total = render_panel(entry, width=width, theme=theme).height
    wp = WindowedPanel(panel_rows(entry, width, theme), width, theme)
    for s in range(0, max(1, total - 260) + 1, 40):  # scroll the whole panel so all bands render
        wp.viewport(s, 260)
    wp.viewport(max(0, total - 260), 260)
    return wp


@given(scale=st.floats(min_value=1.06, max_value=2.5))
@settings(max_examples=25, deadline=None)
def test_scan_cell_interior_round_trips_at_any_scale(scale):
    wp = _measured_panel(scale)
    boxes = wp.scan_boxes()
    assert boxes  # the entry really has scannable cells
    for b in boxes:
        cx, cy = b.x + b.w // 2, b.y + b.h // 2  # a point in the cell's interior
        assert wp.scan_hit(cx, cy) == b  # remaps to its own cell — the picking invariant


@given(scale=st.floats(min_value=1.06, max_value=2.5))
@settings(max_examples=25, deadline=None)
def test_link_interior_round_trips_at_any_scale(scale):
    wp = _measured_panel(scale)
    boxes = wp.link_boxes()
    assert boxes  # the entry really has a clickable cross-reference link
    for lb in boxes:
        cx, cy = lb.x + lb.w // 2, lb.y + lb.h // 2
        assert wp.link_hit(cx, cy) == lb
