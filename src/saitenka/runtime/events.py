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
class ConnectionLost:
    connection_epoch: int


@dataclass(frozen=True, slots=True)
class ConnectionReady:
    """A replacement epoch is live and its observer snapshot is fully queued."""

    connection_epoch: int


@dataclass(frozen=True, slots=True)
class CloseRequested:
    reason: str = "requested"


@dataclass(frozen=True, slots=True)
class StartupHintRequested:
    """IPC is up: post the one thing that can be seen before any overlay exists."""


@dataclass(frozen=True, slots=True)
class StartupReady:
    """The session completed a turn that leaves it interactive — the hint has done its job."""


@dataclass(frozen=True, slots=True)
class RawMpvEvent:
    name: str
    data: object = None


@dataclass(frozen=True, slots=True)
class UserCommand:
    name: str
    args: tuple[object, ...] = ()
    command_id: int | None = None
    coalesced_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.command_id is not None and self.command_id < 0:
            raise ValueError("command IDs must be non-negative")
        if any(command_id < 0 for command_id in self.coalesced_ids):
            raise ValueError("coalesced command IDs must be non-negative")


class CommandOutcome(StrEnum):
    EXECUTED = "executed"
    REJECTED = "rejected"
    UNBOUND = "unbound"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class CommandReason(StrEnum):
    MALFORMED = "malformed"
    UNKNOWN = "unknown"
    HELP_MODAL = "help-modal"
    CUE_RETIRED = "cue-retired"
    DISCONNECTED = "disconnected"
    INTERNAL = "internal"
    LEGACY_REPEAT = "legacy-repeat"
    COALESCED = "coalesced"


@dataclass(frozen=True, slots=True)
class CommandHandled:
    """Typed terminal result of one command-policy or compatibility action."""

    name: str
    owner: Owner | None
    outcome: CommandOutcome
    command_id: int | None = None
    reason: CommandReason | None = None

    def __post_init__(self) -> None:
        if self.command_id is not None and self.command_id < 0:
            raise ValueError("command IDs must be non-negative")
        rejection_reasons = {
            CommandReason.MALFORMED,
            CommandReason.UNKNOWN,
            CommandReason.HELP_MODAL,
            CommandReason.CUE_RETIRED,
            CommandReason.DISCONNECTED,
        }
        valid_reason = {
            CommandOutcome.EXECUTED: self.reason is None,
            CommandOutcome.UNBOUND: self.reason is None,
            CommandOutcome.FAILED: self.reason == CommandReason.INTERNAL,
            CommandOutcome.REJECTED: self.reason in rejection_reasons,
            CommandOutcome.SUPPRESSED: self.reason
            in {CommandReason.LEGACY_REPEAT, CommandReason.COALESCED},
        }[self.outcome]
        if not valid_reason:
            raise ValueError("command outcome and reason are inconsistent")


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


@dataclass(frozen=True, slots=True)
class EffectOutcomeEvent:
    """Ledger-validated completion delivered to its recorded feature owner."""

    effect_id: EffectId
    owner: Owner
    identity: object
    outcome: EffectOutcome
    result: object = None
    error: EffectError | None = None


type RuntimeEvent = (
    ConnectionLost
    | ConnectionReady
    | ConnectionReplaced
    | CloseRequested
    | RawMpvEvent
    | StartupHintRequested
    | StartupReady
    | UserCommand
    | CommandHandled
    | EffectFinished
    | EffectOutcomeEvent
)


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
