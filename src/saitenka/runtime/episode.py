"""`Owner.SESSION`'s episode boundary: mpv finished loading a file, so re-slot onto it.

Holds nothing, and that is the right amount. "A file finished loading" is an announcement, not a
fact to accumulate — whether it is a *new* file is the performer's question, answered against mpv
at the moment it acts. Keeping a path here to compare against would be a second copy of something
mpv already owns, one turn staler than the answer.

So the feature exists to turn an observation into an act. That is a legitimate shape for a slice
feature and worth naming, because the instinct on seeing an empty state is to invent a field for it
— which is the field-without-an-enforcer rule read backwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime.effects import ReslotEpisode
from saitenka.runtime.events import FileLoaded
from saitenka.runtime.state import ReduceResult

if TYPE_CHECKING:
    from saitenka.runtime.events import RuntimeEvent

#: The feature's key inside `Owner.SESSION`'s slice.
EPISODE_FEATURE = "episode"


@dataclass(frozen=True, slots=True)
class EpisodeBoundary:
    """Deliberately empty — see the module docstring."""


def reduce_episode(state: object, event: RuntimeEvent, /) -> ReduceResult:
    """`FeatureReducer` for the file-load boundary.

    The slice broadcasts, so this sees every `Owner.SESSION` event and answers with the state it
    was given for all but one of them.
    """
    assert isinstance(state, EpisodeBoundary)
    if not isinstance(event, FileLoaded):
        return ReduceResult(state)
    return ReduceResult(state, effects=(ReslotEpisode(),))
