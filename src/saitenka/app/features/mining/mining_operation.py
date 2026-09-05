"""One bounded mining operation and its owner-thread publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.app.features.mining import miner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    import threading


LANE = "mining-operation"


class OperationKind(StrEnum):
    WORD = "word"
    BULK = "bulk"


@dataclass(frozen=True, slots=True)
class MiningAction:
    name: str
    args: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class MiningOutcome:
    actions: tuple[MiningAction, ...]


@dataclass(frozen=True, slots=True)
class MiningOperationRequest:
    transaction: miner.MiningTransaction
    kind: OperationKind
    prepared: miner.PreparedCapture
    token: object | None = None
    force: bool = False
    card: object | None = None


_ACTION_NAMES = (
    "toast",
    "reset_capture",
    "captured_image",
    "captured_audio",
    "mark_mined",
    "mined_here",
    "remember_duplicate",
    "preview_existing",
    "preview_mined",
    "record_mined",
    "record_link",
)


def _recording_apply(actions: list[MiningAction]) -> miner.MiningApply:
    def callback(name: str):
        return lambda *args: actions.append(MiningAction(name, args))

    return miner.MiningApply(*(callback(name) for name in _ACTION_NAMES))


def run_operation(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, MiningOperationRequest):
        raise TypeError("invalid mining operation request")
    if cancelled.is_set():
        return MiningOutcome(())
    actions: list[MiningAction] = []
    transaction = replace(request.transaction, apply=_recording_apply(actions))
    if request.kind is OperationKind.BULK:
        miner.bulk_mine(transaction, prepared=request.prepared)
    else:
        assert request.token is not None
        miner.mine_token(
            transaction,
            request.token,
            force=request.force,
            card=request.card,
            animated=request.prepared.animated,
            prepared=request.prepared,
        )
    return MiningOutcome(tuple(actions))


def apply_outcome(outcome: MiningOutcome, apply: miner.MiningApply) -> None:
    callbacks = {name: getattr(apply, name) for name in _ACTION_NAMES}
    for action in outcome.actions:
        callbacks[action.name](*action.args)


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(ipc, LANE, JobLanePolicy(capacity=1), run_operation)
