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
class SendMpvCommand:
    effect_id: EffectId
    owner: Owner
    identity: object
    command: tuple[object, ...]
    deadline: float
    connection_epoch: int

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("mpv command must not be empty")
        if not math.isfinite(self.deadline) or self.deadline < 0:
            raise ValueError("mpv command deadline must be finite and non-negative")
        if self.connection_epoch < 0:
            raise ValueError("connection epoch must be non-negative")


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


@dataclass(frozen=True, slots=True)
class EffectDeadline:
    target_effect_id: EffectId
    due_at: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.due_at) or self.due_at < 0:
            raise ValueError("effect deadline must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class CancelEffect:
    target_effect_id: EffectId
    owner: Owner
    identity: object


@dataclass(frozen=True, slots=True)
class ExpireEffect:
    target_effect_id: EffectId
    deadline: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.deadline) or self.deadline < 0:
            raise ValueError("effect deadline must be finite and non-negative")


type AsyncEffect = SubmitJob | ScheduleTimer | SendMpvCommand
type CoreControl = CancelEffect | ExpireEffect


@dataclass(frozen=True, slots=True)
class EmitDiagnostic:
    name: str
    owner: Owner
    fields: tuple[tuple[str, str | int | float | bool], ...] = ()


@dataclass(frozen=True, slots=True)
class StopSession:
    reason: str


@dataclass(frozen=True, slots=True)
class DetachDiagnostics:
    """Drop the session's telemetry gauge provider.

    Named rather than generic: the vocabulary is closed, so a lifecycle step arrives as its own
    effect instead of an "invoke this callable" escape hatch that would reopen it. The close
    duties in `tests/fixtures/runtime_migration_manifest.json` each declare the effect that
    replaces them, and this is `telemetry`'s.
    """


type LifecycleEffect = StopSession | DetachDiagnostics

#: What leaves the runtime through the effect dispatcher. `StopSession` is absent because the
#: reactor performs it itself, and the two diagnostic/control kinds have their own ports.
type DispatchedEffect = AsyncEffect | DetachDiagnostics


type Effect = AsyncEffect | CoreControl | EmitDiagnostic | LifecycleEffect
