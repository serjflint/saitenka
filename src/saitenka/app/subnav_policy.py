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
    if _flag_on(settings.get("sub-filter-sdh")):
        return True
    if not _flag_off(settings.get("sub-filter-regex-enable")) and _nonempty(
        settings.get("sub-filter-regex")
    ):
        return True
    return _nonempty(settings.get("sub-filter-jsre"))


#: mpv's own spellings for a flag property. It answers JSON bools over the IPC socket, but the same
#: option read as a string — a config file, `--sub-filter-sdh=yes` echoed back — reaches here too,
#: and an identity test against `True` reads every one of those as "no filter".
_TRUE = frozenset({"yes", "true", "1"})
_FALSE = frozenset({"no", "false", "0"})


def _flag_on(value: object) -> bool:
    """Whether a flag reads as set. An unreadable property is not a filter."""
    return _spelling(value) is True


def _flag_off(value: object) -> bool:
    """Whether a flag reads as explicitly clear — the question for a flag that defaults to on, where
    silence has to mean "assume it applies"."""
    return _spelling(value) is False


def _spelling(value: object) -> bool | None:
    """The flag a value spells, or `None` when it spells neither. `1`/`0` are in because an option
    that arrives as a string can arrive as an int by the same routes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value) if value in {0, 1} else None
    if isinstance(value, str):
        text = value.strip().casefold()
        return True if text in _TRUE else (False if text in _FALSE else None)
    return None


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
