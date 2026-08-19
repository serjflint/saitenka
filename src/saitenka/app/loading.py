"""The top-left "loading" spinner shown while dictionaries + scorer load (progressive startup).

Just a bitmap frame builder. A named lifecycle timer advances it on the session thread while plain
subtitles remain usable; FSRS coloring, tooltips, and mining appear when background loading finishes.

The mpv-native startup breadcrumb shares this module's vocabulary (`STARTUP_HINT`, `HintOutcome`,
`HintOperation`) but not its code: it is a session-owned reducer in `app/startup_hint.py`, wired by
`app/session_routes.py`.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

from saitenka.app.toast import render_toast

if TYPE_CHECKING:  # PIL is imported by the renderer (toast); we only need Image for the annotation
    from PIL import Image


log = logging.getLogger("saitenka")

# ASCII spinner — the vendored fonts DON'T cover braille (⠋…), which would render blank; classic
# |/-\ is always covered so the spinner actually animates.
SPINNER = "|/-\\"

# Shown on mpv's OWN OSD the instant IPC connects — the only feedback possible before the first cue.
# ASCII so it renders under any mpv OSD font / user config; mpv (not our vendored fonts) draws it.
STARTUP_HINT = "saitenka starting..."


class HintOutcome(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class HintOperation(Enum):
    SHOW = "show"
    CLEAR = "clear"


def loading_image(text: str, frame: int, size: int = 26) -> Image.Image:
    """One animated frame: ``⠋ <text>…`` rendered as a small toast bitmap."""
    return render_toast(f"{SPINNER[frame % len(SPINNER)]} {text}…", size=size)
