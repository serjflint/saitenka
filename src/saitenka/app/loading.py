"""The top-left "loading" spinner shown while dictionaries + scorer load (progressive startup).

Just a bitmap frame builder. A named lifecycle timer advances it on the session thread while plain
subtitles remain usable; FSRS coloring, tooltips, and mining appear when background loading finishes.

The mpv-native startup breadcrumb is a different thing entirely: a session-owned reducer in
`runtime/startup_hint.py`, which owns its own vocabulary and shares none of this code.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from saitenka.app.toast import render_toast

if TYPE_CHECKING:  # PIL is imported by the renderer (toast); we only need Image for the annotation
    from PIL import Image


log = logging.getLogger("saitenka")

# ASCII spinner — the vendored fonts DON'T cover braille (⠋…), which would render blank; classic
# |/-\ is always covered so the spinner actually animates.
SPINNER = "|/-\\"


def loading_image(text: str, frame: int, size: int = 26) -> Image.Image:
    """One animated frame: ``⠋ <text>…`` rendered as a small toast bitmap."""
    return render_toast(f"{SPINNER[frame % len(SPINNER)]} {text}…", size=size)
