"""Minimal deterministic reactor and effect-lifecycle ledger."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.effects import (
    ApplyPlaybackDeltas,
    AsyncEffect,
    AttachSessionDiagnostics,
    CancelEffect,
    CancelInteractionWork,
    CloseCapabilityActors,
    CloseSessionOverlay,
    CloseSessionStores,
    CloseSessionSurfaces,
    CloseSubtitleRendering,
    CloseWorkerLanes,
    CoreControl,
    DetachDiagnostics,
    DispatchedEffect,
    Effect,
    EffectError,
    EffectId,
    EffectOutcome,
    EmitDiagnostic,
    EstablishRenderSpace,
    ExpireEffect,
    GuardMainRender,
    OpenSessionHistory,
    RegisterInputBindings,
    ReleaseInputCapture,
    RemoveSessionArtifacts,
    ReplaySubtitleSelection,
    ReslotEpisode,
    RetireCueIdentity,
    RunUserCommand,
    SeedOptionalCollaborators,
    StartPropertyObservation,
    StopSession,
)
from saitenka.runtime.events import (
    ConnectionReplaced,
    EffectFinished,
    EventEnvelope,
    EventOrigin,
    RuntimeEvent,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.mailbox import SessionMailbox


class Lifecycle(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class Reducer[StateT](Protocol):
    def __call__(self, state: StateT, event: RuntimeEvent) -> tuple[StateT, tuple[Effect, ...]]: ...


class EffectDispatcher(Protocol):
    def __call__(self, effect: DispatchedEffect, /) -> bool: ...


class ControlDispatcher(Protocol):
    def __call__(self, control: CoreControl, /) -> None: ...


@dataclass(frozen=True, slots=True)
class ReactorSnapshot[StateT]:
    state: StateT
    lifecycle: Lifecycle
    connection_epoch: int
    pending_effects: tuple[EffectId, ...]


class SessionReactor[StateT]:
    """Own reducer state; adapters report completion through :meth:`complete`."""

    def __init__(
        self,
        state: StateT,
        reducer: Reducer[StateT],
        mailbox: SessionMailbox,
        dispatch: EffectDispatcher,
        *,
        diagnostics: Callable[[EmitDiagnostic], None] | None = None,
        control: ControlDispatcher | None = None,
    ) -> None:
        self._state = state
        self._reducer = reducer
        self._mailbox = mailbox
        self._dispatch = dispatch
        self._diagnostics = diagnostics
        self._control = control
        self._lifecycle = Lifecycle.OPEN
        self._connection_epoch = 0
        self._pending: dict[EffectId, AsyncEffect] = {}
        self._highest_effect_id = -1

    @property
    def state(self) -> StateT:
        """The reduced state, without the diagnostic bundle around it.

        `snapshot` sorts the pending-effect table on every read, which is the wrong cost for a
        caller that reads an owner's slot on the observation path.
        """
        return self._state

    @property
    def snapshot(self) -> ReactorSnapshot[StateT]:
        return ReactorSnapshot(
            self._state,
            self._lifecycle,
            self._connection_epoch,
            tuple(sorted(self._pending)),
        )

    def handle(self, envelope: EventEnvelope) -> None:
        payload = envelope.payload
        if self._lifecycle != Lifecycle.OPEN and not isinstance(payload, EffectFinished):
            return
        if isinstance(payload, ConnectionReplaced):
            if not self._replace_connection(payload):
                return
        elif not isinstance(payload, EffectFinished) and (
            envelope.connection_epoch is not None
            and envelope.connection_epoch != self._connection_epoch
        ):
            return
        if isinstance(payload, EffectFinished):
            completion = self._finish(payload)
            if completion is None:
                return
            payload = completion
        self._reduce(payload)

    def owns(self, effect_id: EffectId) -> bool:
        """Did this reactor dispatch that effect and is it still awaiting its completion?

        The ownership question a *caller* needs, distinct from `_finish`'s: the answer decides
        whether a completion is also somebody else's to run.
        """
        return effect_id in self._pending

    def run_until_idle(self) -> int:
        turns = 0
        while envelope := self._mailbox.receive(timeout=0):
            self.handle(envelope)
            turns += 1
        return turns

    def complete(
        self,
        completion: EffectFinished,
        *,
        origin: EventOrigin,
        connection_epoch: int | None = None,
    ) -> bool:
        return self._mailbox.publish_terminal(
            completion,
            origin=origin,
            connection_epoch=connection_epoch,
        )

    def close(self) -> None:
        self._lifecycle = Lifecycle.CLOSING
        for effect in tuple(self._pending.values()):
            completion = EffectFinished(
                effect.effect_id,
                effect.owner,
                effect.identity,
                EffectOutcome.CANCELLED,
            )
            if self.complete(completion, origin=EventOrigin.LIFECYCLE):
                continue
            if not self._mailbox.terminal_enqueued(effect.effect_id):
                del self._pending[effect.effect_id]
                self._mailbox.cancel_reservation(effect.effect_id)
                self._reduce(completion)
        self.run_until_idle()
        if self._pending:
            raise RuntimeError("cannot close with pending effects")
        self._lifecycle = Lifecycle.CLOSED
        self._mailbox.close()

    def _reduce(self, event: RuntimeEvent) -> None:
        self._state, effects = self._reducer(self._state, event)
        for effect in effects:
            self._apply(effect)

    def _apply(self, effect: Effect) -> None:
        if isinstance(effect, EmitDiagnostic):
            if self._diagnostics is not None:
                self._diagnostics(effect)
            return
        if isinstance(effect, StopSession):
            self.close()
            return
        if isinstance(
            effect,
            GuardMainRender
            | EstablishRenderSpace
            | StartPropertyObservation
            | RegisterInputBindings
            | SeedOptionalCollaborators
            | OpenSessionHistory
            | AttachSessionDiagnostics
            | DetachDiagnostics
            | ReleaseInputCapture
            | CloseCapabilityActors
            | CancelInteractionWork
            | CloseWorkerLanes
            | CloseSubtitleRendering
            | CloseSessionStores
            | CloseSessionSurfaces
            | CloseSessionOverlay
            | RemoveSessionArtifacts
            | ReplaySubtitleSelection
            | ReslotEpisode
            | RetireCueIdentity
            | RunUserCommand
            | ApplyPlaybackDeltas,
        ):
            # Fire-and-forget, like `StopSession`: a lifecycle effect carries no ID because there
            # is nothing to correlate a completion to. Reserving a terminal for one would leave a
            # reservation nothing ever retires, and close is when that matters least and costs most.
            #
            # Spelled out rather than read off `FireAndForget`, which would drift: narrowing needs
            # a literal union here, and the alias' `__value__` defeats it. `test_reactor.py` pins
            # the two together — an effect that misses this branch falls through to the async path
            # and dies on the `effect_id` it does not carry.
            self._dispatch(effect)
            return
        if isinstance(effect, (CancelEffect, ExpireEffect)):
            if self._control is not None:
                self._control(effect)
            return
        async_effect = effect
        if async_effect.effect_id.value <= self._highest_effect_id:
            raise ValueError(f"effect ID already used: {async_effect.effect_id.value}")
        self._highest_effect_id = async_effect.effect_id.value
        if self._lifecycle != Lifecycle.OPEN:
            self._reject(async_effect, EffectError.UNAVAILABLE)
            return
        if not self._mailbox.reserve_terminal(async_effect.effect_id):
            self._reject(async_effect, EffectError.OVERLOADED)
            return
        self._pending[async_effect.effect_id] = async_effect
        try:
            accepted = self._dispatch(async_effect)
        except Exception:  # noqa: BLE001  # adapter failures cross the boundary as typed outcomes
            accepted = True
            self.complete(
                EffectFinished(
                    async_effect.effect_id,
                    async_effect.owner,
                    async_effect.identity,
                    EffectOutcome.FAILED,
                    error=EffectError.INTERNAL,
                ),
                origin=EventOrigin.WORKER,
            )
        if not accepted:
            self.complete(
                EffectFinished(
                    async_effect.effect_id,
                    async_effect.owner,
                    async_effect.identity,
                    EffectOutcome.REJECTED,
                ),
                origin=EventOrigin.WORKER,
            )

    def _reject(self, effect: AsyncEffect, error: EffectError) -> None:
        completion = EffectFinished(
            effect.effect_id,
            effect.owner,
            effect.identity,
            EffectOutcome.REJECTED,
            error=error,
        )
        self._reduce(completion)

    def _replace_connection(self, event: ConnectionReplaced) -> bool:
        if event.connection_epoch <= self._connection_epoch:
            return False
        self._connection_epoch = event.connection_epoch
        self._retire_old_connection_effects()
        return True

    def _finish(self, completion: EffectFinished) -> EffectFinished | None:
        accepted = self._pending.get(completion.effect_id)
        if accepted is None:
            # Never retire an effect this reactor did not dispatch. Retiring is a claim of
            # ownership, and the loser of that race does not find out: the other owner's
            # `retire_terminal` returns False, indistinguishable from "already handled", so it
            # drops the completion and whatever awaited it waits forever.
            return None
        if not self._mailbox.retire_terminal(completion.effect_id):
            return None
        del self._pending[completion.effect_id]
        if (
            accepted.connection_epoch is not None
            and accepted.connection_epoch != self._connection_epoch
        ):
            return EffectFinished(
                accepted.effect_id,
                accepted.owner,
                accepted.identity,
                EffectOutcome.STALE,
            )
        if accepted.owner == completion.owner and accepted.identity == completion.identity:
            return completion
        return EffectFinished(
            accepted.effect_id,
            accepted.owner,
            accepted.identity,
            EffectOutcome.FAILED,
            error=EffectError.INVALID_RESULT,
        )

    def _retire_old_connection_effects(self) -> None:
        for effect in tuple(self._pending.values()):
            if effect.connection_epoch is None or effect.connection_epoch == self._connection_epoch:
                continue
            self.complete(
                EffectFinished(
                    effect.effect_id,
                    effect.owner,
                    effect.identity,
                    EffectOutcome.STALE,
                ),
                origin=EventOrigin.LIFECYCLE,
                connection_epoch=effect.connection_epoch,
            )
