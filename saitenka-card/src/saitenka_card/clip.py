"""Spec for a motion (animated) screenshot on a mined card."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AnimatedClip:
    """The on/off toggle plus the quality↔storage levers (``height`` is the primary one). Grouped
    into one object so the mining config and the capture pass a single value instead of six parallel
    args. ``enabled`` gates the capture at the call site; the encoder uses only the encode fields.
    ``fmt``: ``"webp"`` prefers WebP and falls back to GIF; ``"gif"`` forces GIF (universal); anything
    else (av1/mp4 — needs a ``<video>`` template) is unsupported and yields no encode.

    Here rather than beside the ffmpeg call that reads it: this is part of a card's shape, and the
    encoder is the application's. A value the config layer writes and the capture layer reads should
    not drag a subprocess module into everything that touches a mining config.
    """

    enabled: bool = False
    height: int = 480
    fps: int = 12
    quality: int = 75
    max_secs: float = 4.0
    fmt: str = "webp"
