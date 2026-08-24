"""Closed effect vocabulary at the session-runtime boundary."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only: `events` and `playback` import from here, so the runtime edge goes one way.
    from saitenka.runtime.events import UserCommand
    from saitenka.runtime.playback import PlaybackDelta


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


@dataclass(frozen=True, slots=True)
class CloseSessionSurfaces:
    """Remove the session's lifecycle overlays, once no lane can present again.

    Carries no handle for the same reason `DetachDiagnostics` carries no callable: the surfaces
    are a session resource the runtime owns, so the dispatcher already knows where they are. An
    effect that named its target would make the vocabulary depend on the SessionController's field layout.
    """


@dataclass(frozen=True, slots=True)
class CloseSessionOverlay:
    """Close the session's overlay transport, after its surfaces are gone.

    Ordered after `CloseSessionSurfaces` within the one phase, not split across two: the removes
    are queued *through* this transport, so closing it first would strand them.
    """


@dataclass(frozen=True, slots=True)
class ReleaseInputCapture:
    """Hand mpv's forced input section back, while the transport still works.

    Carries no handle, for `CloseSessionSurfaces`' reason: the capture is a session resource the
    runtime owns, so the dispatcher already knows where it is.
    """


#: The setup half's vocabulary. One effect per duty, like the close half's, so what a phase does is
#: readable from the reducer rather than from a phase constant threaded into a generic step.
#:
#: They are NOT `close`-shaped and deliberately do not reuse the resource contract: a startup step
#: *creates*, and an adapter whose `close()` meant "start" would be a lie in the one place the
#: vocabulary has to stay readable.


@dataclass(frozen=True, slots=True)
class GuardMainRender:
    """Pin the build in the log and forbid rasterisation on the render loop's own thread."""


@dataclass(frozen=True, slots=True)
class EstablishRenderSpace:
    """Read the OSD dimensions, so anything placed afterwards has a space to be placed in."""


@dataclass(frozen=True, slots=True)
class StartPropertyObservation:
    """Register the observer set and take its snapshot; reads are event-driven afterwards."""


@dataclass(frozen=True, slots=True)
class RegisterInputBindings:
    """Define the sections and bind the keys, so input routes to this session at all."""


@dataclass(frozen=True, slots=True)
class SeedOptionalCollaborators:
    """Seed the mined set and arm the capability probes — every one of them optional."""


@dataclass(frozen=True, slots=True)
class OpenSessionHistory:
    """Open the session's history row for the file now playing."""


@dataclass(frozen=True, slots=True)
class AttachSessionDiagnostics:
    """Attach the gauge provider and arm the startup-health deadline."""


type StartupEffect = (
    GuardMainRender
    | EstablishRenderSpace
    | StartPropertyObservation
    | RegisterInputBindings
    | SeedOptionalCollaborators
    | OpenSessionHistory
    | AttachSessionDiagnostics
)

STARTUP_EFFECTS = (
    GuardMainRender,
    EstablishRenderSpace,
    StartPropertyObservation,
    RegisterInputBindings,
    SeedOptionalCollaborators,
    OpenSessionHistory,
    AttachSessionDiagnostics,
)


@dataclass(frozen=True, slots=True)
class CloseCapabilityActors:
    """Close the optional collaborators' probes, before anything they answer for goes down."""


@dataclass(frozen=True, slots=True)
class CancelInteractionWork:
    """Cancel the in-flight interaction jobs and retire their metadata broker."""


@dataclass(frozen=True, slots=True)
class CloseWorkerLanes:
    """Signal the workers and drain every job lane, in the one order that is safe.

    Sixteen participants, and the order between them is the contract — the geometry executor stops
    before the state it renders against, and every lane drains before the scratch dir it writes to
    is removed. The budget is not carried here: a lane close needs a *deadline*, and a reducer that
    computed one would be a clock in pure policy. The announcer owns it, as it owns `scratch`.
    """


@dataclass(frozen=True, slots=True)
class CloseSubtitleRendering:
    """Give the subtitle pixels back and close the geometry provider, once no lane can render.

    Three participants, isolated by the dispatcher: giving the pixels back, clearing them, and
    closing whichever of the provider or the pipeline owns the raster.
    """


@dataclass(frozen=True, slots=True)
class CloseSessionStores:
    """Flush and close the session's persistent writers, after nothing renders and before the
    surfaces go. One effect for the three of them because they retire together — the dispatcher
    isolates each, so a store that fails cannot strand the ones behind it."""


@dataclass(frozen=True, slots=True)
class RemoveSessionArtifacts:
    """Delete the session's scratch directory, once nothing can still write to it."""

    path: str


@dataclass(frozen=True, slots=True)
class ReplaySubtitleSelection:
    """The transport was replaced: re-assert the track selection against the new connection.

    A session fact with a subtitle act, which is why it is an effect and not a slice delta — the
    slot holds what was *decided* about the tracks, and re-sending that decision to a connection
    that has never heard it is a performance, not a decision.
    """


@dataclass(frozen=True, slots=True)
class ReslotEpisode:
    """mpv loaded a file: re-slot the overlay and the subtitle source onto it.

    Carries no path, for the reason `FileLoaded` does not: the performer asks mpv what is playing
    when it acts, and skips a file it has already slotted. A path on the effect would be the
    answer as it was one turn ago.
    """


@dataclass(frozen=True, slots=True)
class ApplyPlaybackDeltas:
    """`Owner.PLAYBACK` reduced an observation: here is what that turn published.

    The outbox, as an effect. It carries the deltas rather than pointing at the slice for the
    reason the slice's own docstring gives — `published` is the *last* turn's, and a batch of
    observations overwrites it once per event, so a performer that read it back would apply the
    newest turn several times and the rest never.
    """

    deltas: tuple[PlaybackDelta, ...]


@dataclass(frozen=True, slots=True)
class RunUserCommand:
    """A command arrived from mpv: run it against the session's binding table.

    Carries the command, unlike its neighbours here — the act is *about* this one, and two arriving
    in one batch would be indistinguishable if the performer had to ask what to run. Whether the
    session is in a state to run it is the performer's question, not the reducer's: the answer is a
    read of another feature's state, and a slice's features do not read each other.
    """

    command: UserCommand


@dataclass(frozen=True, slots=True)
class RetireCueIdentity:
    """The transport went away, so the cue on screen describes nothing that is still live.

    Carries no reason. Losing the connection is the whole fact; a reason would be the producer's
    story about it, and no performer branches on one.
    """


type LifecycleEffect = (
    ApplyPlaybackDeltas
    | RunUserCommand
    | ReslotEpisode
    | ReplaySubtitleSelection
    | RetireCueIdentity
    | StopSession
    | StartupEffect
    | DetachDiagnostics
    | ReleaseInputCapture
    | CloseCapabilityActors
    | CancelInteractionWork
    | CloseWorkerLanes
    | CloseSubtitleRendering
    | CloseSessionStores
    | CloseCapabilityActors
    | CancelInteractionWork
    | CloseWorkerLanes
    | CloseSubtitleRendering
    | CloseSessionStores
    | CloseSessionSurfaces
    | CloseSessionOverlay
    | RemoveSessionArtifacts
)

#: Lifecycle effects the reactor hands straight to the dispatcher. No `EffectId`, so no reserved
#: terminal and no completion: nothing correlates to them, and a reservation raised during close
#: is one nothing would ever retire.
type FireAndForget = (
    ApplyPlaybackDeltas
    | RunUserCommand
    | ReslotEpisode
    | ReplaySubtitleSelection
    | RetireCueIdentity
    | StartupEffect
    | DetachDiagnostics
    | ReleaseInputCapture
    | CloseCapabilityActors
    | CancelInteractionWork
    | CloseWorkerLanes
    | CloseSubtitleRendering
    | CloseSessionStores
    | CloseCapabilityActors
    | CancelInteractionWork
    | CloseWorkerLanes
    | CloseSubtitleRendering
    | CloseSessionStores
    | CloseSessionSurfaces
    | CloseSessionOverlay
    | RemoveSessionArtifacts
)

#: What leaves the runtime through the effect dispatcher. `StopSession` is absent because the
#: reactor performs it itself, and the diagnostic/control kinds have their own ports.
type DispatchedEffect = AsyncEffect | FireAndForget


type Effect = AsyncEffect | CoreControl | EmitDiagnostic | LifecycleEffect
