"""The Pillow raster backend: today's renderer behind the :class:`RasterBackend` seam.

This application adapter sits above ``panel`` and the neutral raster protocol; a future cosmic-text
backend replaces it at the composition boundary. Byte-identity with the direct
``LazyPanel.finish() + to_bgra_array`` path is pinned by ``tests/test_raster_backend.py``.
"""

from __future__ import annotations

from saitenka.bgra import to_bgra_array
from saitenka.panel import LazyPanel, Row, Theme
from saitenka.raster.protocol import RasterResult


class PillowBackend:
    """Raster panel rows with Pillow (the existing render stack)."""

    def raster_rows(self, rows: list[Row], width: int, theme: Theme | None = None) -> RasterResult:
        lp = LazyPanel(rows, width, theme or Theme())
        image = lp.finish()
        bgra = to_bgra_array(image)
        return RasterResult(
            bgra=bgra, height=bgra.shape[0], scan_boxes=lp.scan_boxes, link_boxes=lp.link_boxes
        )
