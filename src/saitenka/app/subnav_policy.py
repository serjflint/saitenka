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
    current = index.locate(text=text, sub_start=sub_start, time_pos=time_pos, preferred=nav_idx)
    if current < 0:
        return None
    inside = cue_is_on_screen(
        index.cues[current], text=text, sub_start=sub_start, time_pos=time_pos
    )
    target = index.target(current, delta, inside=inside)
    if target < 0:  # out of range / ambiguous
        return None
    return NavigationTarget(target, index.cues[target])
