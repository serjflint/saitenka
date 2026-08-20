"""Closed event vocabulary consumed by the session runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.runtime.effects import EffectError, EffectId, EffectOutcome, Owner

if TYPE_CHECKING:
    from saitenka.runtime.help import HelpCommand
    from saitenka.runtime.hover import HoverDelays, HoverObservation
    from saitenka.runtime.hover import Intent as HoverIntent
    from saitenka.runtime.playback import RetireReason


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


class StartPhase(StrEnum):
    """How far setup has got — `ClosePhase`'s mirror, and for the same reason.

    A phase is defined by what is already *up*, because that is the only thing a step can depend
    on. `Reader.run` is a sequence, so this ordering is the contract: observing properties before
    the render space is known would seed geometry against dimensions nobody has read.

    Declared whole rather than one phase per migrated duty, exactly as the close half is — a duty
    picks the phase matching where its step already sits instead of inventing one and serialising
    behind the duty that invented the last.
    """

    #: Nothing session-specific yet; the process is what is being pinned.
    PROCESS = "process"
    #: The OSD dimensions are known, so anything can be placed.
    RENDER_SPACE = "render-space"
    #: Property observation is live; reads are event-driven from here on.
    OBSERVERS = "observers"
    #: Sections and keybinds are registered, so input routes to us.
    INPUT = "input"
    #: Optional collaborators are seeded and probed.
    COLLABORATORS = "collaborators"
    #: The session's history row is open.
    HISTORY = "history"
    #: Gauges are attached and the startup-health deadline is armed.
    DIAGNOSTICS = "diagnostics"


@dataclass(frozen=True, slots=True)
class SessionStarting:
    """The setup sequence has reached the runtime's steps for `phase`.

    `SessionClosing`'s mirror. Not a claim on startup — the Reader still announces, and what has
    moved is which side decides *what* the phase does.
    """

    phase: StartPhase = StartPhase.PROCESS


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


#: `Owner.PLAYBACK`'s vocabulary. Two kinds, and the difference decides who acts on the deltas:
#: an *observation* is mpv reporting a fact, so what the projection publishes is news; a
#: *declaration* is the Reader announcing a decision it is already carrying out, so the same
#: deltas reflected back would be that action performed twice.


@dataclass(frozen=True, slots=True)
class PropertyObserved:
    """One ordered mpv property observation. An older epoch cannot change state."""

    name: str
    data: object = None
    connection_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class PropertySeeded:
    """An observer snapshot value: it establishes facts and observes no change."""

    name: str
    data: object = None


@dataclass(frozen=True, slots=True)
class CueIdentityInstalled:
    """The owning reducer bound an identity to the cue and the timing it observed for it."""

    start: object = None
    end: object = None


@dataclass(frozen=True, slots=True)
class CueIdentityRetireRequested:
    """Declaration: the identity is being retired by whoever sends this."""

    reason: RetireReason


@dataclass(frozen=True, slots=True)
class CueTextReplaced:
    """Declaration: a cue the sender chose itself rather than observed from mpv."""

    text: str


@dataclass(frozen=True, slots=True)
class SourceReplaced:
    """Declaration: a new authored subtitle source is live."""

    path: object = None


type PlaybackEvent = (
    PropertyObserved
    | PropertySeeded
    | CueIdentityInstalled
    | CueIdentityRetireRequested
    | CueTextReplaced
    | SourceReplaced
)

#: The runtime form of `PlaybackEvent`, for `isinstance`. A literal tuple rather than the alias'
#: `__value__` for the same reason the reactor spells its fire-and-forget union out.
PLAYBACK_EVENTS = (
    PropertyObserved,
    PropertySeeded,
    CueIdentityInstalled,
    CueIdentityRetireRequested,
    CueTextReplaced,
    SourceReplaced,
)


@dataclass(frozen=True, slots=True)
class EpisodeRetired:
    """The episode ended: every owner retires its per-episode facts.

    The one event that is not any single owner's. It is routed to every slice that registers it,
    and the atomicity comes from the turn boundary — which is the whole reason a lifetime is an
    event here rather than a container somebody rebinds. An owner with no per-episode facts simply
    has no route for it; that is an answer, not a gap.

    No payload: "the episode ended" is the entire fact. A reason would be the *producer's* story
    about why, and no reducer here has a branch for one.
    """


#: `Owner.SUBTITLE`'s vocabulary. Every one is a *declaration*: the sender has already told mpv
#: which track to select, and this is what it selected. Nothing here is an mpv observation —
#: `on_primary_changed` arrives as an mpv event and is turned into `SubtitlePrimaryAdopted` by the
#: code that classified the track, because the classification is the decision, not the property.


@dataclass(frozen=True, slots=True)
class SubtitleStartupConfigured:
    """The startup selection: both role tracks, the active role, and the search language list."""

    jp_sid: int | None
    en_sid: int | None
    language: str
    slang: str


@dataclass(frozen=True, slots=True)
class SubtitleTracksDiscovered:
    """A fresh track-list scan replaced both role slots."""

    jp_sid: int | None
    en_sid: int | None


@dataclass(frozen=True, slots=True)
class SubtitlePrimaryAdopted:
    """One track took a role — a manual cycle, a drag-'n'-drop, or the user's override key."""

    sid: int | None
    language: str


@dataclass(frozen=True, slots=True)
class SubtitleLanguageChanged:
    """The active role changed without either role slot moving."""

    language: str


@dataclass(frozen=True, slots=True)
class SubtitleSecondaryLeased:
    """mpv's secondary track is feeding the translation reveal; `sid` is `None` when released."""

    sid: int | None = None


@dataclass(frozen=True, slots=True)
class SubtitleTrackAnnounced:
    """The track whose name was last put on screen — the guard against re-announcing it."""

    sid: int | None


type SubtitleEvent = (
    SubtitleStartupConfigured
    | SubtitleTracksDiscovered
    | SubtitlePrimaryAdopted
    | SubtitleLanguageChanged
    | SubtitleSecondaryLeased
    | SubtitleTrackAnnounced
)

SUBTITLE_EVENTS = (
    SubtitleStartupConfigured,
    SubtitleTracksDiscovered,
    SubtitlePrimaryAdopted,
    SubtitleLanguageChanged,
    SubtitleSecondaryLeased,
    SubtitleTrackAnnounced,
)


#: `Owner.INTERACTION`'s vocabulary. Unlike SUBTITLE's, these are *observations* — what the cursor
#: is over, and what a dwell did — so the reducer's answer is a set of decisions the sender then
#: performs. That is why this slice has an outbox and SUBTITLE's does not.


@dataclass(frozen=True, slots=True)
class HoverConfigured:
    """The dwell lengths for this session. Configuration, not a hover fact — but it is state the
    reducer reads in the turn, and a reducer that read a clock or a config object would not be
    pure. It arrives as an event for the same reason the track selection does."""

    delays: HoverDelays


@dataclass(frozen=True, slots=True)
class HoverObserved:
    """One hover tick: what the cursor is over, already hit-tested and link-filtered."""

    observation: HoverObservation


@dataclass(frozen=True, slots=True)
class HoverDwellElapsed:
    """A dwell fired. `nest_tail` is the popup showing right now — a scan dwell that outlived what
    armed it must open nothing, and only the current tail can say so."""

    intent: HoverIntent
    nest_tail: str | None = None


@dataclass(frozen=True, slots=True)
class HoverScrolled:
    """A popup was scrolled — an interaction, so its linger stops being pending."""

    nested: bool


@dataclass(frozen=True, slots=True)
class HoverDwellRefused:
    """No deadline could be armed. Its own event, not a flag on the arm: the fail-open/fail-closed
    answer is per intent, and it is a decision the reducer owns rather than the caller."""

    intent: HoverIntent


@dataclass(frozen=True, slots=True)
class HelpCommanded:
    """A shortcut-overlay command, with the document length the paging arms decide against.

    `page_count` rides on the event rather than being read in the turn for the reason the dwell
    lengths do: the number comes from rendering the document against the current screen, which is
    neither pure nor free, and a reducer that reached for it would be neither.
    """

    command: HelpCommand
    page_count: int = 0


@dataclass(frozen=True, slots=True)
class HelpRepaginated:
    """The document was measured again — a resize, or a bindings change. Carries only its length.

    Separate from `HelpCommanded` because nothing the user pressed decides it, and folding it in as
    a null command would make "no command" a value every paging arm has to exclude.
    """

    page_count: int


@dataclass(frozen=True, slots=True)
class PickerOpened:
    """Window 1 went up — a fresh generation, so anything still in flight is now stale."""


@dataclass(frozen=True, slots=True)
class PickerClosed:
    """Window 1 came down, however it came down: the key, a click, or the session closing."""


@dataclass(frozen=True, slots=True)
class PickerListed:
    """A subtitle listing came back. `generation` is which picker asked; the reducer decides
    whether that is still the one on screen. `listing` is opaque — see `runtime/picker.py`."""

    generation: int
    listing: object


@dataclass(frozen=True, slots=True)
class PickerScrolled:
    """A wheel notch over the picker. `count` rides along for the same reason the help page count
    does: it is the length of a list the app rendered, and the clamp is decided against it."""

    steps: int
    count: int


type InteractionEvent = (
    HoverConfigured
    | HoverObserved
    | HoverScrolled
    | HoverDwellElapsed
    | HoverDwellRefused
    | HelpCommanded
    | HelpRepaginated
    | PickerOpened
    | PickerClosed
    | PickerListed
    | PickerScrolled
)

INTERACTION_EVENTS = (
    HoverConfigured,
    HoverObserved,
    HoverScrolled,
    HoverDwellElapsed,
    HoverDwellRefused,
    HelpCommanded,
    HelpRepaginated,
    PickerOpened,
    PickerClosed,
    PickerListed,
    PickerScrolled,
)


#: `Owner.PRESENTATION`'s vocabulary. Declarations, like SUBTITLE's: the sender has already drawn
#: or removed the surface, and this is what it drew.


@dataclass(frozen=True, slots=True)
class TranslationHeld:
    """The manual toggle moved. Only this decides whether the secondary track may be released."""

    held: bool


@dataclass(frozen=True, slots=True)
class TranslationDrawn:
    """What the translation surface is showing; `None` when it was taken down."""

    text: str | None


type PresentationEvent = TranslationHeld | TranslationDrawn

PRESENTATION_EVENTS = (TranslationHeld, TranslationDrawn)


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
    EpisodeRetired
    | ConnectionLost
    | ConnectionReady
    | ConnectionReplaced
    | CloseRequested
    | SessionClosing
    | SessionStarting
    | RawMpvEvent
    | StartupHintRequested
    | StartupReady
    | UserCommand
    | CommandHandled
    | EffectFinished
    | PlaybackEvent
    | SubtitleEvent
    | InteractionEvent
    | PresentationEvent
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
