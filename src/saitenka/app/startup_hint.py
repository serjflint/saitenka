"""The startup hint as a session-owned reducer — D3's first migrated route.

`StartupHintLease` was already this state machine; it just expressed itself with a mutex and four
methods that each recomputed the same "may I clear now?" predicate under the lock. Four states,
four inbound events, one effect kind. Moving it onto the runtime turn removes the lock (a turn is
single-threaded by construction) and leaves the predicate stated once, in `_clearable`.

The reducer is pure per turn but not port-free: emitting a `SendMpvCommand` needs an effect ID, an
absolute deadline and the live connection epoch. Those are bound at construction, so the *turn*
stays a function of (state, event) — which is what makes the FSM testable without a session.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from saitenka.app.loading import STARTUP_HINT, HintOperation, HintOutcome
from saitenka.runtime.effects import (
    EffectError,
    EffectOutcome,
    EmitDiagnostic,
    Owner,
    SendMpvCommand,
)
from saitenka.runtime.events import (
    ConnectionReplaced,
    EffectFinished,
    StartupHintRequested,
    StartupReady,
)
from saitenka.runtime.state import ReduceResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.effects import Effect, EffectId
    from saitenka.runtime.events import RuntimeEvent

#: mpv keeps the hint up this long unaided; the clear is what normally removes it.
_HINT_HOLD_MS = 30_000
_REPLY_TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class StartupHintState:
    outcome: HintOutcome = HintOutcome.PENDING
    ready: bool = False
    reconnected_after_unknown: bool = False
    clear_submitted: bool = False
    clear_connection_epoch: int = -1
    shown_connection_epoch: int = -1


def _lost(event: EffectFinished) -> bool:
    """Did the connection swallow this, leaving mpv's actual state unknown?

    STALE joins DISCONNECTED because the reactor retires an effect issued on a superseded epoch —
    a completion path the lock-based lease never had, since the correlator answered for it instead.
    """
    return event.error is EffectError.DISCONNECTED or event.outcome is EffectOutcome.STALE


def _clearable(state: StartupHintState) -> bool:
    """The one predicate the lease recomputed in four places.

    UNKNOWN means the acknowledgement was lost, not that the hint is absent — so it is clearable
    only once a replacement connection proves mpv is alive again.
    """
    if state.clear_submitted or not state.ready:
        return False
    if state.outcome is HintOutcome.ACCEPTED:
        return True
    return state.outcome is HintOutcome.UNKNOWN and state.reconnected_after_unknown


class StartupHintReducer:
    """`FeatureReducer` for `Owner.SESSION`'s startup-hint slice."""

    def __init__(
        self,
        allocate: Callable[[], EffectId],
        connection_epoch: Callable[[], int],
        clock: Callable[[], float],
    ) -> None:
        self._allocate = allocate
        self._connection_epoch = connection_epoch
        self._clock = clock

    def __call__(self, state: object, event: RuntimeEvent, /) -> ReduceResult:
        assert isinstance(state, StartupHintState)
        if isinstance(event, StartupHintRequested):
            return self._show(state)
        if isinstance(event, StartupReady):
            return self._settle(replace(state, ready=True))
        if isinstance(event, ConnectionReplaced):
            return self._settle(self._reconnected(state))
        if isinstance(event, EffectFinished):
            return self._completed(state, event)
        return ReduceResult(state)

    def _show(self, state: StartupHintState) -> ReduceResult:
        epoch = self._connection_epoch()
        return ReduceResult(
            replace(state, shown_connection_epoch=epoch),
            effects=(self._command(HintOperation.SHOW, (STARTUP_HINT, _HINT_HOLD_MS), epoch),),
        )

    def _reconnected(self, state: StartupHintState) -> StartupHintState:
        if state.outcome is HintOutcome.UNKNOWN:
            return replace(state, reconnected_after_unknown=True)
        return state

    def _completed(self, state: StartupHintState, event: EffectFinished) -> ReduceResult:
        if event.identity is HintOperation.SHOW:
            return self._show_completed(state, event)
        if event.identity is HintOperation.CLEAR:
            return self._clear_completed(state, event)
        return ReduceResult(state)

    def _show_completed(self, state: StartupHintState, event: EffectFinished) -> ReduceResult:
        if state.outcome is not HintOutcome.PENDING:
            return ReduceResult(state)
        accepted = event.outcome is EffectOutcome.SUCCEEDED
        ambiguous = _lost(event)
        outcome = (
            HintOutcome.ACCEPTED
            if accepted
            else (HintOutcome.UNKNOWN if ambiguous else HintOutcome.REJECTED)
        )
        # A newer epoch by the time the reply lands means the connection was already replaced, so
        # the "did mpv get it?" ambiguity is resolved the same way a later ConnectionReplaced would.
        replaced = ambiguous and self._connection_epoch() > state.shown_connection_epoch
        settled = self._settle(
            replace(
                state,
                outcome=outcome,
                reconnected_after_unknown=state.reconnected_after_unknown or replaced,
            )
        )
        return ReduceResult(
            settled.state,
            effects=(self._diagnostic("show", outcome.value), *settled.effects),
        )

    def _clear_completed(self, state: StartupHintState, event: EffectFinished) -> ReduceResult:
        accepted = event.outcome is EffectOutcome.SUCCEEDED
        diagnostic = self._diagnostic("clear", "accepted" if accepted else "rejected")
        if not _lost(event):
            return ReduceResult(state, effects=(diagnostic,))
        # The clear was lost with the connection. Re-arm and let `_settle` resubmit, but only on a
        # genuinely newer epoch — otherwise a disconnect storm resubmits against the dead socket.
        rearmed = replace(state, clear_submitted=False)
        if self._connection_epoch() <= state.clear_connection_epoch:
            return ReduceResult(rearmed, effects=(diagnostic,))
        settled = self._settle(rearmed)
        return ReduceResult(settled.state, effects=(diagnostic, *settled.effects))

    def _settle(self, state: StartupHintState) -> ReduceResult:
        if not _clearable(state):
            return ReduceResult(state)
        epoch = self._connection_epoch()
        return ReduceResult(
            replace(state, clear_submitted=True, clear_connection_epoch=epoch),
            effects=(self._command(HintOperation.CLEAR, ("", 1), epoch),),
        )

    def _command(
        self, operation: HintOperation, args: tuple[object, ...], epoch: int
    ) -> SendMpvCommand:
        return SendMpvCommand(
            self._allocate(),
            Owner.SESSION,
            operation,
            ("show-text", *args),
            self._clock() + _REPLY_TIMEOUT_S,
            epoch,
        )

    def _diagnostic(self, operation: str, outcome: str) -> Effect:
        return EmitDiagnostic(
            "startup.hint", Owner.SESSION, (("operation", operation), ("outcome", outcome))
        )
