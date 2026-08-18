"""Subtitle navigation (Alt+←/→/↓): render the target cue from a parsed subtitle-file index
INSTANTLY, then let mpv's own ``sub-seek`` catch the video up behind it.

Takes ``reader: Reader`` (the AGENTS.md seam pattern) with thin delegating methods on Reader.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app.sub_index import load_index

if TYPE_CHECKING:
    from saitenka.app.controller import Reader


def load_sub_index(reader: Reader, path) -> None:
    """Parse the external subtitle file at ``path`` into a cue index so Alt+←/→/↓ can render the
    target line instantly. Fail-soft: an unreadable/empty/unsupported file RETAINS the prior cues
    (a transient track-switch/resolve failure must not blank a good index) — navigation still falls
    back to a plain mpv sub-seek when there was never an index."""
    idx = load_index(path)
    if idx is None:
        return
    reader._replace_subtitle_source(path, reason="subtitle-index")
    reader._sub_index = idx
    native_geometry = getattr(reader, "native_geometry", None)
    if native_geometry is not None:
        native_geometry.set_source(Path(path), reader=reader)
    from saitenka.app import analysis_overlay

    analysis_overlay.on_index_changed(reader)
    from saitenka.app import sidebar

    sidebar.on_index_changed(reader)
    reader.warm_episode_tokens()  # warm the whole episode's cues into the token cache (bg, best-effort)


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
    # Span covers locate/target AND the render it triggers below — set_subtitle's own "cue_redraw"
    # span nests inside this one, so the span's total duration IS the keypress → drawn latency for
    # the instant-nav path.
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
        # whether prev/next straddle the cue or step onto the upcoming one (see CueIndex.target).
        c = idx.cues[current]
        inside = bool(reader.sub_text.strip())
        if not inside and sub_start is not None:
            inside = c.start <= sub_start < c.end
        if not inside and time_pos is not None:
            inside = c.start <= time_pos < c.end
        tgt = idx.target(current, delta, inside=inside)
        if tgt < 0:
            return False  # out of range / ambiguous → let mpv's sub-seek handle it
        # Captured BEFORE set_subtitle overwrites sub_text — mpv's OWN native sub-seek (fired right
        # after this by the caller) often re-reports THIS pre-nav text as a transient mid-seek value
        # before landing on the real target; reconcile below must not mistake that for a correction.
        reader._nav_prev_text = reader.sub_text
        reader._geometry_cue_hint = idx.cues[tgt]
        try:
            reader.set_subtitle(
                idx.cues[tgt].text,
                provisional_navigation=True,
            )  # instant overlay render (also resets _nav_idx)
        finally:
            reader._geometry_cue_hint = None
        reader._nav_idx = tgt
        # Guard the reconcile: mpv's sub-text briefly reads empty (or the pre-nav cue) mid-seek;
        # ignoring that avoids reverting the render before it settles. ~1s covers a slow seek.
        reader._sub_settle_until = time.monotonic() + 1.0
    return True


def reconcile_sub_text(reader: Reader, text: str) -> None:
    """Poll-loop hook: adopt mpv's current ``sub-text`` when it changed. mpv is the source of truth
    (it corrects the line if our instant-nav index guessed wrong), EXCEPT for two transient values
    mpv emits mid-seek right after a manual sub-nav: an empty blip, and mpv re-reporting the PRE-nav
    cue's text before it catches up to the real target (confirmed live: a real ``sub-seek`` fired
    from inside the target cue's own span briefly re-reports the cue we just navigated AWAY from).
    Naively adopting either would flash the wrong text and — worse — silently reset ``_nav_idx``
    (any ``set_subtitle`` call does), breaking next/next/next chaining even though the render was
    already correct. Swallow both within the settle window."""
    # Empty is a stable retired state: the first transition already cleared every interaction
    # surface, so reinstalling the same empty observation would only repeat teardown every poll.
    if text == reader.sub_text and (not reader._cue_retired or not text.strip()):
        return
    identity_reinstall = text == reader.sub_text and reader._cue_retired
    within_settle = time.monotonic() < reader._sub_settle_until
    if within_settle and (
        not text.strip() or (text == reader._nav_prev_text and not identity_reinstall)
    ):
        return
    # Only spans an actual cue change (guarded above), not every poll tick — sibling to sub_nav's
    # "sub_seek" span, but for changes mpv itself drove (native sub-seek key bound in the lua
    # script, or a normal cue advance during playback) rather than our own instant-nav.
    # set_subtitle's "cue_redraw" span nests inside, so this span's duration is the best proxy this
    # process has for "mpv-observed sub-text change → overlay drawn" — it can't see when the seek
    # command itself was issued (that's mpv-internal / lua-side).
    with otel_metrics.instrumented(
        otel_metrics.sub_text_reconcile_duration_ms, "sub_text_reconcile"
    ):
        nav_idx = reader._nav_idx
        if identity_reinstall and within_settle and reader._nav_provisional_cue_counted:
            reader.set_subtitle(text, revise_session_cue=True)
        else:
            reader.set_subtitle(text)
        reader._nav_provisional_cue_counted = False
        if identity_reinstall:
            reader._nav_idx = nav_idx
    reader._sub_settle_until = 0.0
