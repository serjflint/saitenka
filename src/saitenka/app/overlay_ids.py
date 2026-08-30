"""Collision-free OSD slots in back-to-front paint order.

mpv composites larger ``overlay-add`` IDs last. Keeping the ordering here makes paint priority
reviewable independently of where each surface is rendered.
"""

from __future__ import annotations

from enum import IntEnum


class OverlayId(IntEnum):
    # Subtitle plane.
    SUB = 1
    # Raster color over mpv's subtitle glyphs; separate because its cue lifetime differs.
    OVERPAINT = 2
    TRANS = 3

    # Contextual interaction.
    TIP = 4
    NESTED = 5  # a scan popup opened by hovering a word *inside* the tooltip

    # Panels, matching the inverse of the topmost-first input order.
    PREVIEW = 6
    SIDEBAR = 7
    ANALYSIS = 8  # reserved for #66; keeps the two large surfaces collision-free
    PICKER = 9  # Window 1: the jimaku subtitle-source download picker
    HELP = 10

    # Session chrome stays visible over content and panels.
    LOADING = 11  # top-left "loading dictionaries" spinner during progressive startup
    TOAST = 12
