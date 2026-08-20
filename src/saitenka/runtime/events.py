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


class ClosePhase(StrEnum):
    """How far teardown has got. Close is a sequence, so its participants are not interchangeable.

    A phase is defined by what is already gone, because that is the only thing a participant can
    depend on. Declared in teardown order, and the whole sequence exists up front rather than one
    phase per migrated duty: a duty picks the phase matching where its step already sits, instead
    of inventing one and serialising behind the duty that invented the last.

    `Reader.close` is a sequence, so this ordering *is* the contract — announcing everything at
    `PARTICIPANTS` would run a participant tens of steps early, removing overlays while a lane can
    still add one. A phase with no effects yet is legitimate; it marks the seam for the duty that
    lands there next.
    """

    #: Optional collaborators are down; everything else is still live.
    CAPABILITIES = "capabilities"
    #: The runtime's own participants, while every collaborator and the transport still work.
    PARTICIPANTS = "participants"
    #: Every job lane has drained, so no background work can still land.
    LANES = "lanes"
    #: Geometry and the subtitle pipeline are closed; nothing renders.
    RENDERING = "rendering"
    #: Session stores are flushed and closed.
    STORES = "stores"
    #: Nothing can present again, so overlays and their transport can go.
    SURFACES = "surfaces"
    #: Nothing can write any more.
    ARTIFACTS = "artifacts"


@dataclass(frozen=True, slots=True)
class SessionClosing:
    """The close sequence has reached the runtime's participants for `phase`.

    Distinct from `CloseRequested`, which is the *stop* signal a disconnect or an overloaded
    mailbox raises to end the session. This is the session announcing that it is tearing down, so
    the owners that registered lifetimes can retire them. One event for both would mean claiming
    `CloseRequested` away from the legacy router, which is what turns a lost transport into a
    stopped session.

    `scratch` is the session's per-run directory, carried here because the runtime outlives no
    Reader and the path is created per Reader — the *decision* to remove it, once and only after
    everything that could still write to it has stopped, is what has moved.
    """

    phase: ClosePhase = ClosePhase.PARTICIPANTS
    scratch: str | None = None


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


type RuntimeEvent = (
    ConnectionLost
    | ConnectionReady
    | ConnectionReplaced
    | CloseRequested
    | SessionClosing
    | RawMpvEvent
    | StartupHintRequested
    | StartupReady
    | UserCommand
    | CommandHandled
    | EffectFinished
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
