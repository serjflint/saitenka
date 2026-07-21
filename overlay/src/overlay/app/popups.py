"""Popup view state: the cached tooltip panel + the per-popup view.

``TipPanel`` (formerly controller's ``_TipPanel``) is a cached, viewport-first-rendered panel.
``PopupView`` (formerly ``_Nested``) is the unified per-popup VIEW state — anchor, viewport,
scroll, screen rect, linger timer, dirty flag. The nested scan popup uses it fully today; the base
tooltip's exploded ``_tip_*`` attributes migrate onto a second PopupView in a later stage (kept
exploded for now so the hover FSM and its tests stay untouched).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

    from overlay.app.tokenize import Token

from overlay.mpvio.osd import to_bgra_array
from overlay.panel import LazyPanel


class TipPanel:
    """A cached tooltip panel: a :class:`LazyPanel` plus its premultiplied BGRA, rendered
    viewport-first.

    ``render_head`` paints just the visible top on the hover thread; ``finish`` renders the
    deferred below-the-fold def bodies (on a prefetch worker, or inline when no worker is
    available)."""

    def __init__(self, lazy: LazyPanel, reading: str):
        self.lazy = lazy
        self.reading = reading
        self.image: Image.Image | None = None  # rendered RGBA (partial head, then full)
        self.bgra: np.ndarray | None = None  # premultiplied BGRA — scroll slices this
        self._lock = threading.Lock()

    @property
    def complete(self) -> bool:
        return self.lazy.complete

    def render_head(self, min_h: int) -> None:
        if self.image is not None:  # fast-path: already rendered — never blocks
            return
        with self._lock:
            if self.image is None:  # first paint only; re-hover reuses it
                self.image = self.lazy.render_to(min_h)
                self.bgra = to_bgra_array(self.image)

    def finish(self) -> None:
        # Render the tail OUTSIDE the lock so a concurrent render_head() call from the main
        # thread can fast-path on self.image is not None without waiting for a slow tail render.
        # The lazy panel itself is single-writer (finish() is called from at most one worker at a
        # time per panel key), so no additional lock is needed around render/finish.
        if self.lazy.complete:
            return
        new_image = self.lazy.finish()
        new_bgra = to_bgra_array(new_image)
        with self._lock:
            self.image = new_image
            self.bgra = new_bgra


class PopupView:
    """State for one popup view — today the nested scan popup (a tooltip opened by hovering a word
    *inside* another tooltip). Kept in one object so the base tooltip's own state stays untouched."""

    def __init__(self):
        self.state: TipPanel | None = None  # TipPanel of the shown word
        self.key: tuple | None = None  # its panel-cache key (finisher matches the shown popup)
        self.token: Token | None = None  # the inner Token (for mining via the popup's ⊕)
        self.word: str | None = None  # inner word surface — dedup against re-opening
        self.tail: str | None = None  # scan-cell tail that opened it — skip re-scanning
        self.bgra: np.ndarray | None = None
        self.xy: tuple[int, int] = (0, 0)
        self.view_h = 0
        self.scroll = 0
        self.rect: tuple[int, int, int, int] | None = None  # screen rect, for hit-testing
        self.hide_at = 0.0
        self.dirty = False  # a background finish grew the popup → re-upload
