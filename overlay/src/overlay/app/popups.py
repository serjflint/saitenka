"""Popup view state: the cached tooltip panel + the per-popup view.

``TipPanel`` (formerly controller's ``_TipPanel``) is a cached, viewport-first-rendered panel.
``PopupView`` (formerly ``_Nested``) is the unified per-popup VIEW state — anchor, viewport,
scroll, screen rect, linger timer, dirty flag. The nested scan popup uses it fully today; the base
tooltip's exploded ``_tip_*`` attributes migrate onto a second PopupView in a later stage (kept
exploded for now so the hover FSM and its tests stay untouched).
"""

from __future__ import annotations

import threading
import zlib
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL import Image

    from overlay.app.tokenize import Token

from overlay.mpvio.osd import to_bgra_array
from overlay.panel import LazyPanel

# zlib level for cached panels: ~16x on the mostly-transparent BGRA, ~3 ms to decompress a tall panel.
# Compress runs on the prefetch worker (off the hot path); decompress only when a cached panel becomes
# the ACTIVE tooltip (~once per hover), never per scroll frame.
_COMPRESS_LEVEL = 3


class TipPanel:
    """A cached tooltip panel. To keep the LRU panel cache small, the rendered panel is retained ONLY
    as a zlib-compressed premultiplied-BGRA blob — the raw RGBA image and the :class:`LazyPanel`'s
    per-row sub-images (a second and third full-size copy) are dropped. One tall multi-dict entry went
    from ~tens of MB × 3 copies to a single ~sub-MB blob.

    ``render_head`` paints just the visible top on the hover thread; ``finish`` renders the deferred
    below-the-fold bodies (on a prefetch worker, or inline). :meth:`bgra` decompresses to a live array
    when the panel becomes active — the caller keeps that array, so scrolling slices it with no further
    decompress."""

    def __init__(self, lazy: LazyPanel, reading: str):
        self.lazy = lazy
        self.reading = reading
        self._packed: bytes | None = None  # zlib(premultiplied BGRA bytes)
        self._shape: tuple[int, int, int] | None = None  # (h, w, 4) of the stored panel
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """True once the head (or full panel) has been rendered and stored — cheap; no decompress."""
        return self._packed is not None

    @property
    def shape(self) -> tuple[int, int, int]:
        """(h, w, 4) of the stored panel — for placement without decompressing."""
        assert self._shape is not None, "shape read before the panel was rendered"
        return self._shape

    @property
    def complete(self) -> bool:
        return self.lazy.complete

    def _store(self, img: Image.Image) -> None:
        bgra = to_bgra_array(img)  # contiguous premultiplied BGRA (H, W, 4)
        self._packed = zlib.compress(bgra.tobytes(), _COMPRESS_LEVEL)
        self._shape = bgra.shape

    def bgra(self) -> np.ndarray | None:
        """Decompress to a fresh premultiplied-BGRA array, or None if not rendered yet. Read-only is
        fine — ``_blit_panel`` copies its viewport slice before drawing. Call when the panel becomes
        active and keep the result for scrolling."""
        if self._packed is None or self._shape is None:
            return None
        return np.frombuffer(zlib.decompress(self._packed), dtype=np.uint8).reshape(self._shape)

    def render_head(self, min_h: int) -> None:
        if self._packed is not None:  # fast-path: already rendered — never blocks
            return
        with self._lock:
            if self._packed is None:  # first paint only; re-hover reuses it
                self._store(self.lazy.render_to(min_h))

    def finish(self) -> None:
        # Render the tail OUTSIDE the lock so a concurrent render_head() call from the main thread can
        # fast-path on `ready` without waiting for a slow tail render. The lazy panel is single-writer
        # (finish() runs on at most one worker at a time per panel key), so no lock around the render.
        if self.lazy.complete:
            return
        new_image = self.lazy.finish()
        with self._lock:
            self._store(new_image)
        self.lazy.release_rows()  # the BGRA blob supersedes the per-row images → free them


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
