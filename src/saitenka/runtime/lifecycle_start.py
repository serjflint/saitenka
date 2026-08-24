"""The session's setup steps, as a reducer — `lifecycle_close`'s mirror.

`SessionController.run` names every step itself, so each one's *lifetime* is split between the subsystem that
owns it and a line in a setup sequence far away. This feature holds the ones the runtime has taken
over: `SessionStarting` arrives, it emits their effects, and `run` no longer names them.

Latched for the close half's reason inverted: a session that reconnects and re-announces a phase
must not re-register a section or reopen a history row somebody has since replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime.effects import (
    AttachSessionDiagnostics,
    EstablishRenderSpace,
    GuardMainRender,
    OpenSessionHistory,
    RegisterInputBindings,
    SeedOptionalCollaborators,
    StartPropertyObservation,
)
from saitenka.runtime.events import SessionStarting, StartPhase
from saitenka.runtime.state import ReduceResult

if TYPE_CHECKING:
    from saitenka.runtime.effects import Effect
    from saitenka.runtime.events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class LifecycleStartState:
    done: frozenset[StartPhase] = frozenset()


def _effects(event: SessionStarting) -> tuple[Effect, ...]:
    """What this phase brings up. Empty is legitimate — a phase nobody has migrated into yet.

    Spelled out per phase rather than driven from a table, exactly as the close half is: the
    migration checker attributes a call to its enclosing function, so a table would put every
    duty's evidence at module level where no duty can claim it.
    """
    if event.phase is StartPhase.PROCESS:
        return (GuardMainRender(),)
    if event.phase is StartPhase.RENDER_SPACE:
        return (EstablishRenderSpace(),)
    if event.phase is StartPhase.OBSERVERS:
        return (StartPropertyObservation(),)
    if event.phase is StartPhase.INPUT:
        return (RegisterInputBindings(),)
    if event.phase is StartPhase.COLLABORATORS:
        return (SeedOptionalCollaborators(),)
    if event.phase is StartPhase.HISTORY:
        return (OpenSessionHistory(),)
    # No trailing `return ()`: every phase is migrated, so one would be unreachable. A phase
    # added without a step reinstates it — and the type checker says so at the time.
    return (AttachSessionDiagnostics(),)


def reduce_lifecycle_start(state: object, event: RuntimeEvent, /) -> ReduceResult:
    """`FeatureReducer` for the setup steps `Owner.SESSION` owns."""
    assert isinstance(state, LifecycleStartState)
    if not isinstance(event, SessionStarting) or event.phase in state.done:
        return ReduceResult(state)
    return ReduceResult(LifecycleStartState(state.done | {event.phase}), effects=_effects(event))
