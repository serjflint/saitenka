"""`Owner.PLAYBACK`'s feature: one slice of state and the reducer that advances it.

The reduction lives here rather than at the call site so there is one of it. A Reader with no
session runtime installed drives this reducer directly; a Reader with one drives it through the
reactor's route table. Only the *store* differs — an inline second copy of the reduction would be
the untested path that drifts, which is the argument `LocalJobLane` already makes for job lanes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from saitenka.runtime.events import (
    PLAYBACK_EVENTS,
    CueIdentityInstalled,
    CueIdentityRetireRequested,
    CueTextReplaced,
    PropertyObserved,
    PropertySeeded,
    SourceReplaced,
)
from saitenka.runtime.playback import PlaybackProjection, PlaybackState
from saitenka.runtime.state import ReduceResult

if TYPE_CHECKING:
    from saitenka.runtime.events import PlaybackEvent, RuntimeEvent
    from saitenka.runtime.playback import PlaybackDelta


@dataclass(frozen=True, slots=True)
class PlaybackSlice:
    """The projection plus what the turn that produced this slice published.

    `published` is an outbox, not a fact: it holds exactly the deltas of the event just reduced,
    and the next event replaces it. It sits on the state because `ReduceResult` has nowhere else
    for an output that is neither an effect nor an event a registered owner reduces — the
    consumers are still the Reader's `_apply_playback_delta` branches. When SUBTITLE, INTERACTION
    and PRESENTATION have slices, these become the turn's internal events and this field goes.
    """

    state: PlaybackState = field(default_factory=PlaybackState)
    published: tuple[PlaybackDelta, ...] = ()


class PlaybackReducer:
    """Reduce one playback event. Pure: the projection performs no I/O and holds no state."""

    def __init__(self, projection: PlaybackProjection | None = None) -> None:
        self._projection = projection if projection is not None else PlaybackProjection()

    @property
    def projection(self) -> PlaybackProjection:
        return self._projection

    def reduce(self, slice_: PlaybackSlice, event: PlaybackEvent) -> PlaybackSlice:
        projection = self._projection
        state = slice_.state
        match event:
            case PropertyObserved(name=name, data=data, connection_epoch=epoch):
                projected = projection.observe(state, name, data, connection_epoch=epoch)
                return PlaybackSlice(projected.state, projected.deltas)
            case PropertySeeded(name=name, data=data):
                return PlaybackSlice(projection.seed(state, name, data))
            case CueIdentityInstalled(start=start, end=end):
                return PlaybackSlice(projection.install(state, start=start, end=end))
            # The three declarations publish nothing, and that is the vocabulary's point rather
            # than a gap: the sender is the one retiring the identity or replacing the source, so
            # handing it `CueIdentityRetired` back would re-enter the teardown it is already in.
            # They become publishing events when the owner that acts on them is a slice.
            case CueIdentityRetireRequested(reason=reason):
                return PlaybackSlice(projection.retire(state, reason)[0])
            case CueTextReplaced(text=text):
                return PlaybackSlice(projection.cue_replaced(state, text))
            case SourceReplaced(path=path):
                return PlaybackSlice(projection.source_replaced(state, path).state)

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, PlaybackSlice)
        assert isinstance(event, PLAYBACK_EVENTS)
        return ReduceResult(self.reduce(state, event))
