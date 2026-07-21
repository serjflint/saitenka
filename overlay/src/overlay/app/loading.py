"""The top-left "loading" spinner shown while dictionaries + scorer load (progressive startup).

Just a bitmap frame builder — the controller drives it from its own poll loop (it owns the mpv IPC
once running, so there's no separate thread to race it), drawing plain subtitles immediately and
swapping in FSRS coloring + tooltips + mining once the background load finishes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from overlay.app.toast import render_toast

if TYPE_CHECKING:  # PIL is imported by the renderer (toast); we only need Image for the annotation
    from PIL import Image

# ASCII spinner — the vendored fonts DON'T cover braille (⠋…), which would render blank; classic
# |/-\ is always covered so the spinner actually animates.
SPINNER = "|/-\\"


def loading_image(text: str, frame: int, size: int = 26) -> Image.Image:
    """One animated frame: ``⠋ <text>…`` rendered as a small toast bitmap."""
    return render_toast(f"{SPINNER[frame % len(SPINNER)]} {text}…", size=size)
