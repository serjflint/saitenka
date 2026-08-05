"""The top-left "loading" spinner shown while dictionaries + scorer load (progressive startup).

Just a bitmap frame builder — the controller drives it from its own poll loop (it owns the mpv IPC
once running, so there's no separate thread to race it), drawing plain subtitles immediately and
swapping in FSRS coloring + tooltips + mining once the background load finishes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from overlay.app.toast import render_toast

if TYPE_CHECKING:  # PIL is imported by the renderer (toast); we only need Image for the annotation
    from PIL import Image

log = logging.getLogger("overlay")

# ASCII spinner — the vendored fonts DON'T cover braille (⠋…), which would render blank; classic
# |/-\ is always covered so the spinner actually animates.
SPINNER = "|/-\\"

# Shown on mpv's OWN OSD the instant IPC connects — the only feedback possible before the first cue.
# ASCII so it renders under any mpv OSD font / user config; mpv (not our vendored fonts) draws it.
STARTUP_HINT = "saitenka starting..."


def loading_image(text: str, frame: int, size: int = 26) -> Image.Image:
    """One animated frame: ``⠋ <text>…`` rendered as a small toast bitmap."""
    return render_toast(f"{SPINNER[frame % len(SPINNER)]} {text}…", size=size)


def show_startup_hint(ipc, *, screenshot: bool = False) -> None:
    """Post the startup breadcrumb on mpv's native OSD the moment IPC connects. This is the ONLY thing
    that can appear during mpv's file-load window: our own overlay doesn't exist yet, and the main
    thread is then blocked in a ``get_property`` waiting on mpv, so nothing of ours can draw. mpv shows
    it as soon as its VO is up; :func:`clear_startup_hint` removes it on the first cue (else it times
    out). Skipped for screenshots (it would land in the capture)."""
    if screenshot:
        return
    try:
        ipc.command("show-text", STARTUP_HINT, 30000)  # 30s ceiling in case no cue ever clears it
    except Exception:
        log.debug("startup OSD hint failed", exc_info=True)


def clear_startup_hint(ipc) -> None:
    """Drop the startup breadcrumb (empty show-text with a 1ms life) once the overlay is live."""
    try:
        ipc.command("show-text", "", 1)
    except Exception:
        log.debug("clear startup OSD hint failed", exc_info=True)
