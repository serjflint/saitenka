"""Pure subtitle-navigation target selection.

Choosing which cue Alt+left/right/down lands on is a function of the parsed index and the facts
already read from mpv. Keeping it separate from the render/seek that follows means the awkward
part — deciding whether a cue is actually *on screen* or merely the next one in a gap — can be
exercised without an IPC fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from saitenka.subtitles import Cue, CueIndex


@dataclass(frozen=True, slots=True)
class NavigationTarget:
    index: int
    cue: Cue
    #: Other cues that were on screen alongside the one this step was measured from. Carried so a
    #: step taken in an overlap is distinguishable in a trace from an unambiguous one — the two
    #: land the user in different places and only one of them is obviously right.
    overlapping: int = 0


#: mpv's subtitle filters (`mp_sub_filter_opts`, `sd_ass.c`) that decide a cue never appears.
#: `regex`/`jsre` drop the whole packet; SDH rewrites the text and mpv drops what it empties.
FILTER_OPTIONS = (
    "sub-filter-sdh",
    "sub-filter-regex-enable",
    "sub-filter-regex",
    "sub-filter-jsre",
)


def filters_can_drop_a_cue(settings: Mapping[str, object]) -> bool:
    """Whether mpv may be hiding cues the parsed index still contains.

    The index comes from the subtitle *file*; these filters run between the file and the screen, so
    a filtered episode's index holds cues mpv will never show and Alt+←/→ can step onto silence.

    Reproducing the filters is not the answer. `jsre` is JavaScript evaluated by mpv's own engine,
    and matching a regex engine's dialect by eye is how a navigation lands one cue off with nothing
    reporting it. So this only *detects* them, and the caller falls back to mpv's `sub-seek` — which
    cannot land on a dropped cue, because mpv is the one dropping them.
    """
    if settings.get("sub-filter-sdh") is True:
        return True
    if settings.get("sub-filter-regex-enable") is not False and _nonempty(
        settings.get("sub-filter-regex")
    ):
        return True
    return _nonempty(settings.get("sub-filter-jsre"))


def _nonempty(value: object) -> bool:
    """An mpv string-list property reads back as a list, but a `None` from an unreadable property
    and an empty list must both mean "no filter"."""
    return bool(value) if isinstance(value, (list, tuple)) else bool(str(value or "").strip())


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
