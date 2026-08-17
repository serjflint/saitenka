"""Closed event vocabulary consumed by the session runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from saitenka.runtime.effects import EffectError, EffectId, EffectOutcome, Owner


class EventOrigin(StrEnum):
    LIFECYCLE = "lifecycle"
    MPV = "mpv"
    USER = "user"
    WORKER = "worker"
    TIMER = "timer"
    PRESENTATION = "presentation"


@dataclass(frozen=True, slots=True)
class ConnectionReplaced:
    connection_epoch: int


@dataclass(frozen=True, slots=True)
class CloseRequested:
    reason: str = "requested"


@dataclass(frozen=True, slots=True)
class RawMpvEvent:
    name: str
    data: object = None


@dataclass(frozen=True, slots=True)
class UserCommand:
    name: str
    args: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectFinished:
    effect_id: EffectId
    owner: Owner
    identity: object
    outcome: EffectOutcome
    result: object = None
    error: EffectError | None = None

    def __post_init__(self) -> None:
        if self.outcome == EffectOutcome.FAILED and self.error is None:
            raise ValueError("failed effects require an error code")
        if (
            self.outcome not in {EffectOutcome.FAILED, EffectOutcome.REJECTED}
            and self.error is not None
        ):
            raise ValueError("only failed or rejected effects carry an error code")


type RuntimeEvent = ConnectionReplaced | CloseRequested | RawMpvEvent | UserCommand | EffectFinished


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    sequence: int
    occurred_at: float
    origin: EventOrigin
    connection_epoch: int | None
    payload: RuntimeEvent

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("event sequence must be non-negative")
        if self.occurred_at < 0:
            raise ValueError("event time must be non-negative")
        if self.connection_epoch is not None and self.connection_epoch < 0:
            raise ValueError("connection epoch must be non-negative")
