"""Closed effect vocabulary at the session-runtime boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class Owner(StrEnum):
    SESSION = "session"
    PLAYBACK = "playback"
    SUBTITLE = "subtitle"
    INTERACTION = "interaction"
    PRESENTATION = "presentation"


class EffectOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STALE = "stale"


class EffectError(StrEnum):
    UNSUPPORTED_INPUT = "unsupported-input"
    UNAVAILABLE = "unavailable"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    INVALID_RESULT = "invalid-result"
    DISCONNECTED = "disconnected"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True, order=True)
class EffectId:
    """Session-local, monotonically increasing effect sequence."""

    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("effect IDs must be non-negative")


@dataclass(frozen=True, slots=True)
class SubmitJob:
    effect_id: EffectId
    owner: Owner
    identity: object
    lane: str
    request: object
    connection_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class ScheduleTimer:
    effect_id: EffectId
    owner: Owner
    identity: object
    timer: str
    due_at: float
    connection_epoch: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.due_at) or self.due_at < 0:
            raise ValueError("timer deadline must be finite and non-negative")


type AsyncEffect = SubmitJob | ScheduleTimer


@dataclass(frozen=True, slots=True)
class EmitDiagnostic:
    name: str
    owner: Owner
    fields: tuple[tuple[str, str | int | float | bool], ...] = ()


@dataclass(frozen=True, slots=True)
class StopSession:
    reason: str


type Effect = AsyncEffect | EmitDiagnostic | StopSession
