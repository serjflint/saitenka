"""Popup view state: the cached tooltip panel + the per-popup view.

``Panel`` is a cached, windowed-rendered tooltip panel — a :class:`WindowedPanel` over the entry's
rows plus its reading. ``PopupView`` is the per-popup VIEW state — anchor, viewport, scroll, screen
rect, linger timer, dirty flag. The nested scan popup uses it fully; the base tooltip keeps its own
exploded ``_tip_*`` attributes (so the hover FSM and its tests stay untouched).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from overlay.mpvio.osd import to_bgra_array

if TYPE_CHECKING:
    import numpy as np

    from overlay.app.tokenize import Token
    from overlay.render.banded import WindowedPanel


class Panel:
    """A cached tooltip panel: a windowed (banded) renderer over the entry's rows plus its reading.

    The windowed engine composites each viewport O(viewport) from a per-block pixel cache (retained
    zlib-compressed, so a cached/warmed panel keeps the old blob path's memory profile), estimates the
    full height as blocks measure, and owns hit-testing. One path for the base tooltip and every
    nested / kanji / search popup — no whole-panel blob, no deferred-tail finish."""

    def __init__(self, windowed: WindowedPanel, reading: str):
        self.windowed = windowed
        self.reading = reading

    @classmethod
    def from_rows(cls, rows, width: int, reading: str) -> Panel:
        """Wrap ``rows`` in the windowed engine (the sole tooltip compositor). ``compress=True`` retains
        each rendered block zlib-compressed, so a cached/warmed panel keeps the old blob's memory
        profile. Shared by the base tooltip and the nested/kanji/search popups."""
        # Lazy imports: overlay.body_block depends on render.document, so a module-level import of
        # render_body_block would cycle back through .render at the package level. render_block_fn is
        # injected for the same reason (see WindowedPanel).
        from overlay.panel import render_body_block
        from overlay.render.banded import WindowedPanel

        return cls(
            WindowedPanel(rows, width, compress=True, render_block_fn=render_body_block), reading
        )

    @property
    def width(self) -> int:
        return self.windowed.width

    @property
    def full_height(self) -> int:
        """Best current estimate of the whole-panel height — exact for measured blocks, converging as
        the rest render. Drives placement, the scroll clamp, and the scrollbar."""
        return self.windowed.full_height

    @property
    def retained_nbytes(self) -> int:
        """On-heap footprint of the retained (compressed) blocks — the panel-cache gauge."""
        return self.windowed.retained_nbytes

    def render_head(self, min_h: int) -> None:
        """Warm + measure the head prefix so placement has a real height and a later hover is a cache
        hit (also the speculative-prefetch entry point). Cheap: renders only the head blocks."""
        self.windowed.measure_to(min_h)

    def viewport(self, scroll: int, view_h: int, overscan: int = 0) -> np.ndarray:
        """Composite the ``[scroll, scroll+view_h)`` viewport as a premultiplied BGRA array. ``overscan``
        renders one screen of blocks below the fold and keeps them warm for the next wheel notch."""
        return to_bgra_array(self.windowed.viewport(scroll, view_h, overscan=overscan))


class PopupView:
    """State for one popup view — today the nested scan popup (a tooltip opened by hovering a word
    *inside* another tooltip). Kept in one object so the base tooltip's own state stays untouched."""

    def __init__(self):
        self.state: Panel | None = None  # Panel of the shown word
        self.key: tuple | None = None  # its panel-cache key
        self.token: Token | None = None  # the inner Token (for mining via the popup's ⊕)
        self.word: str | None = None  # inner word surface — dedup against re-opening
        self.tail: str | None = None  # scan-cell tail that opened it — skip re-scanning
        self.xy: tuple[int, int] = (0, 0)
        self.view_h = 0
        self.scroll = 0
        self.rect: tuple[int, int, int, int] | None = None  # screen rect, for hit-testing
        self.hide_at = 0.0
