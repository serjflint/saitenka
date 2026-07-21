"""A small animated "loading" overlay in the top-left corner (like SubMiner's spinner), shown while
the dictionaries + scorer load so the first frames aren't a blank screen.

Runs on its own daemon thread and drives mpv's OSD over IPC. **Only the loader thread touches IPC while
it runs** — the caller is busy building deps (no mpv IPC) — so single-flight ``command()`` holds; the
caller MUST ``stop()`` it before driving mpv again. Cleared (and its overlay removed) on ``stop()``.
"""

from __future__ import annotations

import logging
import threading

from overlay.app.toast import render_toast
from overlay.mpvio.osd import Overlay

log = logging.getLogger(__name__)

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # braille spinner
_LOADING_OID = 9  # above the reader's overlay ids (1–6); always cleared before the reader draws


class LoadingIndicator:
    """Animate a top-left "⠋ saitenka loading…" until :meth:`stop`. Usable as a context manager."""

    def __init__(self, ipc, interval: float = 0.08):  # ~0.8s/rotation, like SubMiner's CSS spinner
        self._ov = Overlay(ipc)
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, text: str = "saitenka loading") -> None:
        self._thread = threading.Thread(
            target=self._run, args=(text,), name="saitenka-loading", daemon=True
        )
        self._thread.start()

    def _run(self, text: str) -> None:
        i = 0
        try:
            while not self._stop.is_set():
                img = render_toast(f"{_FRAMES[i % len(_FRAMES)]} {text}…", size=26)
                try:
                    self._ov.show(img, x=24, y=24, oid=_LOADING_OID)
                except Exception:  # mpv quit / IPC gone — nothing more to draw
                    break
                i += 1
                self._stop.wait(self._interval)
        finally:
            try:
                self._ov.hide(_LOADING_OID)
            except Exception:
                log.debug("loading overlay hide failed", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self) -> LoadingIndicator:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
