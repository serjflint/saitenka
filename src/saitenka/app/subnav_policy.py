"""Pure subtitle-navigation target selection (WP4.5).

Choosing which cue Alt+left/right/down lands on is a function of the parsed index and the facts
already read from mpv. Keeping it separate from the render/seek that follows means the awkward
part — deciding whether a cue is actually *on screen* or merely the next one in a gap — can be
exercised without an IPC fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.subtitles import Cue, CueIndex


@dataclass(frozen=True, slots=True)
class NavigationTarget:
    index: int
    cue: Cue
    #: Other cues that were on screen alongside the one this step was measured from. Carried so a
    #: step taken in an overlap is distinguishable in a trace from an unambiguous one — the two
    #: land the user in different places and only one of them is obviously right.
    overlapping: int = 0


def cue_is_on_screen(
    cue: Cue, *, text: str, sub_start: float | None, time_pos: float | None
) -> bool:
    """Whether `cue` is showing now, rather than being the upcoming one across a gap.

    This decides whether prev/next straddle the current cue or step onto the upcoming one. Text is
    the strongest evidence; during a seek it is stale, so the timings answer instead.
    """
    if text.strip():
        return True
    if sub_start is not None and cue.start <= sub_start < cue.end:
        return True
    return time_pos is not None and cue.start <= time_pos < cue.end


def resolve_target(
    index: CueIndex,
    *,
    delta: int,
    text: str,
    sub_start: float | None,
    time_pos: float | None,
    nav_idx: int,
) -> NavigationTarget | None:
    """The cue `delta` steps away, or None to let mpv's own sub-seek handle it.

    Chaining works while the video seek is still in flight (time-pos/sub-start are stale): after a
    nav render `text` is the line we drew, so `locate` finds it by text and `nav_idx` disambiguates
    duplicates, letting next/next/next step forward predictably.
    """
    if len(index) == 0:
        return None
    active = index.locate_active(
        text=text, sub_start=sub_start, time_pos=time_pos, preferred=nav_idx
    )
    if not active.located:
        return None
    current = active.position
    inside = cue_is_on_screen(
        index.cues[current], text=text, sub_start=sub_start, time_pos=time_pos
    )
    target = index.target(current, delta, inside=inside)
    if target < 0:  # out of range / ambiguous
        return None
    return NavigationTarget(target, index.cues[target], active.overlapping)


def anchor_delay(
    cue_starts: tuple[float, ...], *, playhead: float, current_delay: float
) -> float | None:
    """The `sub-delay` that snaps the cue the user is hearing to start now.

    The cue nearest the playhead is chosen against the *delayed* timeline, because that is what is
    on screen — so a second anchor refines the first rather than fighting it.
    """
    if not cue_starts:
        return None
    nearest = min(cue_starts, key=lambda start: abs((start + current_delay) - playhead))
    return playhead - nearest
