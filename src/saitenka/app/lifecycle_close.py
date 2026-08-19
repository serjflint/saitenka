"""The session's close participants, as a reducer — D3's second migrated route.

`Reader.close` names every participant itself, so each one's *lifetime* is split between the
subsystem that installs it and a line in a teardown table far away. This feature holds the ones the
runtime has taken over: `SessionClosing` arrives, it emits their effects, and the Reader's table no
longer mentions them.

Latched, because close is not idempotent by accident: a second `SessionClosing` (a stop racing an
explicit close) must not re-detach something an owner has since reinstalled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime.effects import DetachDiagnostics, RemoveSessionArtifacts
from saitenka.runtime.events import ClosePhase, SessionClosing
from saitenka.runtime.state import ReduceResult

if TYPE_CHECKING:
    from saitenka.runtime.effects import Effect
    from saitenka.runtime.events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class LifecycleCloseState:
    done: frozenset[ClosePhase] = frozenset()


def _effects(event: SessionClosing) -> tuple[Effect, ...]:
    """What this phase retires. Empty is legitimate — a phase nobody has migrated into yet."""
    if event.phase is ClosePhase.PARTICIPANTS:
        return (DetachDiagnostics(),)
    if event.scratch is not None:
        return (RemoveSessionArtifacts(event.scratch),)
    return ()


def reduce_lifecycle_close(state: object, event: RuntimeEvent, /) -> ReduceResult:
    """`FeatureReducer` for the close participants `Owner.SESSION` owns."""
    assert isinstance(state, LifecycleCloseState)
    if not isinstance(event, SessionClosing) or event.phase in state.done:
        return ReduceResult(state)
    return ReduceResult(LifecycleCloseState(state.done | {event.phase}), effects=_effects(event))
