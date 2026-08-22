"""Revision-fenced state for stable presentation slots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from saitenka.runtime.effects import EffectError, EffectOutcome


class SurfaceAction(StrEnum):
    PRESENT = "present"
    REMOVE = "remove"


class SurfaceStatus(StrEnum):
    PENDING = "pending"
    PRESENT = "present"
    ABSENT = "absent"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SurfaceTransaction:
    slot: str
    revision: int
    action: SurfaceAction

    def __post_init__(self) -> None:
        if not self.slot:
            raise ValueError("surface slot must not be empty")
        if self.revision <= 0:
            raise ValueError("surface revision must be positive")


@dataclass(frozen=True, slots=True)
class SurfaceTransactionOutcome:
    transaction: SurfaceTransaction
    outcome: EffectOutcome
    error: EffectError | None = None


@dataclass(frozen=True, slots=True)
class SurfaceSnapshot:
    slot: str
    desired_revision: int
    acknowledged_revision: int
    status: SurfaceStatus


class SurfaceRuntime:
    """Allocate revisions and accept acknowledgements only for the latest desired state."""

    def __init__(self) -> None:
        self._slots: dict[str, SurfaceSnapshot] = {}
        self._lock = Lock()

    def request(self, slot: str, action: SurfaceAction) -> SurfaceTransaction:
        with self._lock:
            current = self._slots.get(slot)
            revision = 1 if current is None else current.desired_revision + 1
            self._slots[slot] = SurfaceSnapshot(
                slot,
                revision,
                0 if current is None else current.acknowledged_revision,
                SurfaceStatus.PENDING,
            )
        return SurfaceTransaction(slot, revision, action)

    def finish(self, result: SurfaceTransactionOutcome) -> bool:
        transaction = result.transaction
        with self._lock:
            current = self._slots.get(transaction.slot)
            if (
                current is None
                or current.desired_revision != transaction.revision
                or current.status is not SurfaceStatus.PENDING
            ):
                return False
            if result.outcome is EffectOutcome.SUCCEEDED:
                status = (
                    SurfaceStatus.PRESENT
                    if transaction.action is SurfaceAction.PRESENT
                    else SurfaceStatus.ABSENT
                )
            else:
                status = SurfaceStatus.FAILED
            self._slots[transaction.slot] = SurfaceSnapshot(
                transaction.slot,
                transaction.revision,
                transaction.revision,
                status,
            )
        return True

    def snapshot(self, slot: str) -> SurfaceSnapshot | None:
        with self._lock:
            return self._slots.get(slot)

    def settled(self) -> bool:
        """True when every slot's latest request has been acknowledged.

        "Staged" and "on screen" are different states, and a deterministic capture needs the
        second: a screenshot taken while a slot is still PENDING photographs whatever was there
        before it.
        """
        with self._lock:
            return all(slot.status is not SurfaceStatus.PENDING for slot in self._slots.values())
