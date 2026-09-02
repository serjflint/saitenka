"""`Owner.SUBTITLE`'s track-selection facts, as an immutable state and a pure reduction.

The state is what mpv was *told*, never what it was read back as. That is the whole reason it is
here: `sid` is written fire-and-forget and echoed asynchronously, so a reader that consults the
property mid-switch is answered with the track being replaced. The selection is a decision, and a
decision has a writer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: The role sentinels, spelled as their string values: `runtime/` sits below `app/` and names no
#: app module. `app.languages` owns the constants and the two agree by value, as they already do
#: on disk (session stats, backlog) and against mpv's track tags.
MAIN_ROLE = "jp"


@dataclass(frozen=True, slots=True)
class SubtitleTrackState:
    """Which mpv track plays which role for this episode.

    `announced_sid` rides along rather than living beside it: it is a fact *about* the selection
    (which track the user was last told about) and every path that changes the selection is a path
    that may need to announce it. Two homes for it is how a re-announcement of the same track
    becomes possible.
    """

    jp_sid: int | None = None
    en_sid: int | None = None
    language: str = MAIN_ROLE
    slang: str = "ja,jpn,jp"
    secondary_sid: int | None = None
    announced_sid: int | None = None
    # Selection criterion for the role still named `en_sid` for compatibility.
    second_slang: str = "en"

    @property
    def primary_sid(self) -> int | None:
        """The track the active role is showing."""
        return self.jp_sid if self.language == MAIN_ROLE else self.en_sid

    @property
    def translation_sid(self) -> int | None:
        """The track the *other* role would show — what the reveal leases as mpv's secondary."""
        return self.en_sid if self.language == MAIN_ROLE else self.jp_sid


def adopt(state: SubtitleTrackState, sid: int | None, language: str) -> SubtitleTrackState:
    """Give `sid` the `language` role, and take it away from the other one if it held it.

    The steal is the rule worth having in one place: the override key ("treat what is on screen as
    Japanese") can hand the main role a track already filed as the translation, and leaving it in
    both would lease the reveal the very track it is revealing.
    """
    if language == MAIN_ROLE:
        return replace(state, jp_sid=sid, en_sid=None if state.en_sid == sid else state.en_sid)
    return replace(state, en_sid=sid, jp_sid=None if state.jp_sid == sid else state.jp_sid)
