"""Subtitle-domain data."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    """A display-ready subtitle cue with times in seconds."""

    start: float
    end: float
    text: str
