"""Subtitle navigation (Alt+←/→/↓): render the target cue from a parsed subtitle-file index
INSTANTLY, then let mpv's own ``sub-seek`` catch the video up behind it.

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from overlay import otel_metrics
from overlay.app.sub_index import load_index

if TYPE_CHECKING:
    from overlay.app.controller import Reader


def load_sub_index(reader: Reader, path) -> None:
    """Parse the external subtitle file at ``path`` into a cue index so Alt+←/→/↓ can render the
    target line instantly. Fail-soft: an unreadable/empty/unsupported file just leaves the index
    None → navigation falls back to a plain mpv sub-seek."""
    reader._sub_index = load_index(path)


def _get_float(reader: Reader, prop: str) -> float | None:
    v = reader._get(prop)  # a direct get_property is fine: nav keys are rare, not per-tick
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sub_nav(reader: Reader, delta: int) -> bool:
    """Render the cue ``delta`` steps away (-1 prev / 0 replay / +1 next) in the overlay right now,
    from the parsed index — the perceived-instant half of subtitle navigation. Returns True if it
    drew a target line. The caller still issues the real ``sub-seek`` so the video catches up; the
    poll loop reconciles to mpv's ``sub-text`` once the seek settles.

    Chaining works while the video seek is still in flight (time-pos/sub-start are stale): after a
    nav render ``sub_text`` is the line we drew, so ``locate`` finds it by text and ``_nav_idx``
    disambiguates duplicates — next/next/next steps forward predictably."""
    idx = reader._sub_index
    if idx is None or len(idx) == 0:
        return False
    with otel_metrics.instrumented(otel_metrics.sub_seek_duration_ms, "sub_seek"):
        sub_start = _get_float(reader, "sub-start")
        time_pos = _get_float(reader, "time-pos")
        current = idx.locate(
            text=reader.sub_text, sub_start=sub_start, time_pos=time_pos, preferred=reader._nav_idx
        )
        if current < 0:
            return False
        # Is a cue actually on screen now, or is `current` just the upcoming one in a gap? A sub is
        # showing (non-empty text), or the position falls inside current's span. This decides
        # whether prev/next straddle the cue or step onto the upcoming one (see SubIndex.target).
        c = idx.cues[current]
        inside = bool(reader.sub_text.strip())
        if not inside and sub_start is not None:
            inside = c.start <= sub_start < c.end
        if not inside and time_pos is not None:
            inside = c.start <= time_pos < c.end
        tgt = idx.target(current, delta, inside=inside)
    if tgt < 0:
        return False  # out of range / ambiguous → let mpv's sub-seek handle it
    reader.set_subtitle(idx.cues[tgt].text)  # instant overlay render (also resets _nav_idx)
    reader._nav_idx = tgt
    # Guard the reconcile: mpv's sub-text briefly reads empty mid-seek; ignoring that avoids a blank
    # flicker before it settles on the real (matching) cue text. ~1s covers a slow seek.
    reader._sub_settle_until = time.monotonic() + 1.0
    return True


def reconcile_sub_text(reader: Reader, text: str) -> None:
    """Poll-loop hook: adopt mpv's current ``sub-text`` when it changed. mpv is the source of truth
    (it corrects the line if our instant-nav index guessed wrong), EXCEPT for the empty blip mpv
    emits mid-seek right after a manual sub-nav — swallow that within the settle window so the
    overlay doesn't flash blank before the real cue text lands."""
    if text == reader.sub_text:
        return
    if text.strip() or time.monotonic() >= reader._sub_settle_until:
        reader.set_subtitle(text)
        reader._sub_settle_until = 0.0
