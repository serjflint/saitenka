"""Application I/O adapter for the pure subtitle cue index."""

from __future__ import annotations

import logging
from pathlib import Path

from saitenka_subtitles import CueIndex, parse_cues

log = logging.getLogger(__name__)

__all__ = ["load_index"]


def load_index(path: str | Path) -> CueIndex | None:
    """Load a subtitle file, degrading to ``None`` when it cannot produce cues."""
    subtitle_path = Path(path)
    try:
        content = subtitle_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.debug("sub index: cannot read %s", subtitle_path, exc_info=True)
        return None
    cues = parse_cues(content, subtitle_path.name)
    if not cues:
        log.debug("sub index: no cues parsed from %s", subtitle_path)
        return None
    log.info("sub index: %d cues from %s", len(cues), subtitle_path.name)
    return CueIndex(cues)
