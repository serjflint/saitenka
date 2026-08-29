"""Text-free diagnostic records shared by runtime traces and reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.runtime.effects import (
        EffectError,
        EffectId,
        EffectOutcome,
        EmitDiagnostic,
        Owner,
    )


class DiagnosticKind(StrEnum):
    RUNTIME_TURN = "runtime-turn"
    MAILBOX_ADMISSION = "mailbox-admission"
    EFFECT_LIFECYCLE = "effect-lifecycle"
    PROJECTION_TRANSITION = "projection-transition"
    PIXEL_OWNERSHIP_TRANSITION = "pixel-ownership-transition"
    DEGRADATION_TRANSITION = "degradation-transition"
    CLOSE_STEP = "close-step"


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    kind: DiagnosticKind
    owner: Owner
    event: str | None = None
    effect_id: EffectId | None = None
    outcome: EffectOutcome | None = None
    error: EffectError | None = None
    identity_kind: str | None = None
    revision: int | None = None
    lane: str | None = None
    depth: int | None = None
    capacity: int | None = None
    connection_epoch: int | None = None
    surface_slot: str | None = None
    reason: str | None = None
    queue_ms: float | None = None
    execute_ms: float | None = None
    apply_ms: float | None = None

    def __post_init__(self) -> None:
        numeric = (
            self.revision,
            self.depth,
            self.capacity,
            self.connection_epoch,
            self.queue_ms,
            self.execute_ms,
            self.apply_ms,
        )
        if any(value is not None and value < 0 for value in numeric):
            raise ValueError("diagnostic numeric fields must be non-negative")
        for value in (
            self.event,
            self.identity_kind,
            self.lane,
            self.surface_slot,
            self.reason,
        ):
            if value is not None and (len(value) > 64 or any(char.isspace() for char in value)):
                raise ValueError("diagnostic labels must be bounded identifiers")


class RuntimeLedger:
    """What the runtime emitted, controlled and refused to route — as counts, for one session.

    Three namespaces answer one runtime-observability question: `diagnostic:` is what a reducer
    reported, `control:` is a cancel/expire that reached its port, and `unrouted:` is an event
    outside the declared owner graph.

    Bounded, like every other runtime queue: a key set that grows with traffic would make an
    unknown event stream a leak. Past `capacity` distinct keys, new ones are refused and counted
    as `ledger:overflow` — a saturated ledger says so instead of lying by omission.
    """

    def __init__(self, *, capacity: int = 256) -> None:
        self._counts: Counter[str] = Counter()
        self._capacity = capacity
        self._overflow = 0

    def diagnostic(self, effect: EmitDiagnostic) -> None:
        fields = "".join(f",{key}={value}" for key, value in effect.fields)
        self._bump(f"diagnostic:{effect.owner.value}:{effect.name}{fields}")

    def control(self, key: str) -> None:
        self._bump(f"control:{key}")

    def unrouted(self, key: str) -> None:
        self._bump(f"unrouted:{key}")

    @property
    def counts(self) -> dict[str, int]:
        counts = dict(self._counts)
        if self._overflow:
            counts["ledger:overflow"] = self._overflow
        return counts

    def _bump(self, key: str) -> None:
        if key not in self._counts and len(self._counts) >= self._capacity:
            self._overflow += 1
            return
        self._counts[key] += 1
