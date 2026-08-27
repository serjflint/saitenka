"""The session's close participants, as a reducer — D3's second migrated route.

`SessionController.close` names every participant itself, so each one's *lifetime* is split between the
subsystem that installs it and a line in a teardown table far away. This feature holds the ones the
runtime has taken over: `SessionClosing` arrives, it emits their effects, and the SessionController's table no
longer mentions them.

Latched, because close is not idempotent by accident: a second `SessionClosing` (a stop racing an
explicit close) must not re-detach something an owner has since reinstalled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime.effects import (
    CancelInteractionWork,
    CloseCapabilityActors,
    CloseSessionOverlay,
    CloseSessionStores,
    CloseSessionSurfaces,
    CloseSubtitleRendering,
    CloseWorkerLanes,
    DetachDiagnostics,
    ReleaseInputCapture,
    RemoveSessionArtifacts,
)
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
    if event.phase is ClosePhase.CAPABILITIES:
        return (CloseCapabilityActors(),)
    if event.phase is ClosePhase.PARTICIPANTS:
        # Order is the contract, and it is the order the SessionController's table already had: cancel the
        # interaction work, then the capture — a write to mpv, so it goes while the transport still
        # works — and only then diagnostics, which is the point past which the session stops being
        # observable.
        return (CancelInteractionWork(), ReleaseInputCapture(), DetachDiagnostics())
    if event.phase is ClosePhase.LANES:
        return (CloseWorkerLanes(),)
    if event.phase is ClosePhase.RENDERING:
        return (CloseSubtitleRendering(),)
    if event.phase is ClosePhase.STORES:
        return (CloseSessionStores(),)
    if event.phase is ClosePhase.SURFACES:
        return (CloseSessionSurfaces(),)
    if event.phase is ClosePhase.OVERLAY:
        return (CloseSessionOverlay(),)
    if event.scratch is not None:
        return (RemoveSessionArtifacts(event.scratch),)
    return ()


def reduce_lifecycle_close(state: object, event: RuntimeEvent, /) -> ReduceResult:
    """`FeatureReducer` for the close participants `Owner.SESSION` owns."""
    assert isinstance(state, LifecycleCloseState)
    if not isinstance(event, SessionClosing) or event.phase in state.done:
        return ReduceResult(state)
    return ReduceResult(LifecycleCloseState(state.done | {event.phase}), effects=_effects(event))
