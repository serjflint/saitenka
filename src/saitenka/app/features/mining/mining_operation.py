"""One bounded mining operation and its owner-thread publication."""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.app.features.mining import miner
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    from collections.abc import Callable

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


class MiningActionJournal:
    """Transfer completed worker effects without waiting for the terminal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._actions: list[MiningAction] = []

    def record(self, name: str, args: tuple[object, ...]) -> None:
        with self._lock:
            self._actions.append(MiningAction(name, args))

    def drain(self) -> tuple[MiningAction, ...]:
        with self._lock:
            actions = tuple(self._actions)
            self._actions.clear()
        return actions


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
    journal: MiningActionJournal | None = None


_ACTION_NAMES = tuple(field.name for field in fields(miner.MiningApply))


def _recording_apply(journal: MiningActionJournal) -> miner.MiningApply:
    def callback(name: str) -> Callable[..., None]:
        return lambda *args: journal.record(name, args)

    return miner.MiningApply(
        toast=callback("toast"),
        reset_capture=callback("reset_capture"),
        captured_image=callback("captured_image"),
        captured_audio=callback("captured_audio"),
        mark_mined=callback("mark_mined"),
        mined_here=callback("mined_here"),
        remember_duplicate=callback("remember_duplicate"),
        preview_existing=callback("preview_existing"),
        preview_mined=callback("preview_mined"),
        record_mined=callback("record_mined"),
        record_link=callback("record_link"),
        commit_mined=callback("commit_mined"),
    )


def run_operation(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, MiningOperationRequest):
        raise TypeError("invalid mining operation request")
    if cancelled.is_set() or request.cancelled.is_set():
        return MiningOutcome(())
    shared_journal = request.journal is not None
    journal = request.journal or MiningActionJournal()

    def outcome(plan: object | None = None) -> MiningOutcome:
        actions = () if shared_journal else journal.drain()
        return MiningOutcome(actions, plan)

    def is_cancelled() -> bool:
        return cancelled.is_set() or request.cancelled.is_set()

    transaction = replace(
        request.transaction,
        apply=_recording_apply(journal),
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
        return outcome(plan)
    assert request.prepared is not None and request.plan is not None
    if request.kind is OperationKind.BULK:
        assert isinstance(request.plan, miner.BulkMinePlan)
        miner.commit_bulk(transaction, request.plan, request.prepared)
    else:
        assert isinstance(request.plan, miner.TokenMinePlan)
        miner.commit_token(transaction, request.plan, request.prepared)
    return outcome()


def apply_outcome(outcome: MiningOutcome, apply: miner.MiningApply) -> None:
    callbacks = {name: getattr(apply, name) for name in _ACTION_NAMES}
    for action in outcome.actions:
        callbacks[action.name](*action.args)


def configure_runtime_job(ipc) -> JobSubmitter | None:
    return configure_lane(ipc, LANE, JobLanePolicy(capacity=1), run_operation)
