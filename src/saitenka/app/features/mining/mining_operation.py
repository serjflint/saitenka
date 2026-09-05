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


class OperationStage(StrEnum):
    PREFLIGHT = "preflight"
    COMMIT = "commit"


@dataclass(frozen=True, slots=True)
class MiningAction:
    name: str
    args: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class MiningOutcome:
    actions: tuple[MiningAction, ...]
    plan: object | None = None


@dataclass(frozen=True, slots=True)
class MiningOperationRequest:
    transaction: miner.MiningTransaction
    kind: OperationKind
    stage: OperationStage
    cancelled: threading.Event
    token: object | None = None
    force: bool = False
    card: object | None = None
    plan: object | None = None
    prepared: miner.PreparedCapture | None = None


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
    if cancelled.is_set() or request.cancelled.is_set():
        return MiningOutcome(())
    actions: list[MiningAction] = []

    def is_cancelled() -> bool:
        return cancelled.is_set() or request.cancelled.is_set()

    transaction = replace(
        request.transaction,
        apply=_recording_apply(actions),
        cancelled=is_cancelled,
    )
    if request.stage is OperationStage.PREFLIGHT:
        plan: miner.BulkMinePlan | miner.TokenMinePlan | None
        if request.kind is OperationKind.BULK:
            plan = miner.preflight_bulk(transaction)
        else:
            assert request.token is not None
            plan = miner.preflight_token(
                transaction, request.token, force=request.force, card=request.card
            )
        return MiningOutcome(tuple(actions), plan)
    assert request.prepared is not None and request.plan is not None
    if request.kind is OperationKind.BULK:
        assert isinstance(request.plan, miner.BulkMinePlan)
        miner.commit_bulk(transaction, request.plan, request.prepared)
    else:
        assert isinstance(request.plan, miner.TokenMinePlan)
        miner.commit_token(transaction, request.plan, request.prepared)
    return MiningOutcome(tuple(actions))


def apply_outcome(outcome: MiningOutcome, apply: miner.MiningApply) -> None:
    callbacks = {name: getattr(apply, name) for name in _ACTION_NAMES}
    for action in outcome.actions:
        callbacks[action.name](*action.args)


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(ipc, LANE, JobLanePolicy(capacity=1), run_operation)
