"""The raster backend seam: swap Pillow for Rust/cosmic-text without a rewrite.

The INPUT is the existing pure-data row model (``panel.panel_rows`` output — deferred row thunks
over ``sc/`` structured-content blocks); the OUTPUT is premultiplied BGRA — already the canonical
interchange at ``mpvio/osd.py`` — plus the LAYOUT-produced hit geometry (``model.ScanBox`` /
``model.LinkBox``; a raster swap must never change hit geometry).

This protocol covers the one-shot full raster. The incremental viewport-first path
(``LazyPanel.render_to`` / ``finish``) stays backend-internal for now — a future backend supplies
its own incremental strategy behind the same result type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from overlay.model import LinkBox, ScanBox

if TYPE_CHECKING:
    import numpy as np

    from overlay.panel import Row, Theme


@dataclass(frozen=True)
class RasterResult:
    """One rastered panel: premultiplied BGRA (H, W, 4) + layout hit geometry."""

    bgra: np.ndarray
    height: int
    scan_boxes: list[ScanBox] = field(default_factory=list)
    link_boxes: list[LinkBox] = field(default_factory=list)


@runtime_checkable
class RasterBackend(Protocol):
    """A panel rasteriser. Implementations: ``raster.pillow_backend.PillowBackend`` (today),
    ``overlay._native`` PyO3 cosmic-text (future — must declare free-threading support)."""

    def raster_rows(self, rows: list[Row], width: int, theme: Theme | None = None) -> RasterResult:
        """Raster the composed panel for ``rows`` at ``width`` px; theme defaults to the panel
        theme. The result's BGRA is premultiplied and contiguous."""
        ...
