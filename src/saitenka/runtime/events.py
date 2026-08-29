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


@dataclass(frozen=True, slots=True)
class StartupHintRequested:
    """IPC is up: post the one thing that can be seen before any overlay exists."""


@dataclass(frozen=True, slots=True)
class StartupReady:
    """The session completed a turn that leaves it interactive — the hint has done its job."""


@dataclass(frozen=True, slots=True)
class FileLoaded:
    """mpv finished loading a file.

    Typed rather than left in `RawMpvEvent`, for the reason `UserCommand` already is: `owner_of`
    dispatches on the payload's type, so an observation that needs an owner needs a name. It
    carries no path — whether this is a *new* file is the performer's question, and it reads the
    answer from mpv when it acts rather than from a field that was true when the event was built.
    """


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
            CommandOutcome.SUPPRESSED: self.reason == CommandReason.COALESCED,
        }[self.outcome]
        if not valid_reason:
            raise ValueError("command outcome and reason are inconsistent")


#: `Owner.PLAYBACK`'s vocabulary. Two kinds, and the difference decides who acts on the deltas:
#: an *observation* is mpv reporting a fact, so what the projection publishes is news; a
#: *declaration* is the SessionController announcing a decision it is already carrying out, so the same
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


@dataclass(frozen=True, slots=True)
class SidebarShown:
    """The sidebar went up. `capacity` is how many rows fit on this screen — a render fact, so it
    rides on the event for the same reason the help page count does."""

    active: int
    capacity: int


@dataclass(frozen=True, slots=True)
class SidebarHidden:
    """The sidebar came down."""


@dataclass(frozen=True, slots=True)
class SidebarReindexed:
    """A new cue index arrived, so the scroll and the follow anchor point at rows that are gone."""


@dataclass(frozen=True, slots=True)
class SidebarViewSelected:
    """The user picked one of the sidebar's views."""

    view: str


@dataclass(frozen=True, slots=True)
class SidebarScrolled:
    """A wheel notch over the sidebar. `held` is whether the manual-hold deadline was armed —
    a refusal the reducer decides against, not one the caller resolves on its own."""

    steps: int
    maximum: int
    held: bool


@dataclass(frozen=True, slots=True)
class SidebarFollowed:
    """One chance to re-centre on the active row. `geometry` is opaque: see `runtime/sidebar.py`."""

    active: int
    capacity: int
    geometry: object


@dataclass(frozen=True, slots=True)
class SidebarHoldReleased:
    """The manual-scroll hold's deadline landed."""


@dataclass(frozen=True, slots=True)
class TipNavPushed:
    """A cross-reference is being navigated to. `view` is what it replaces, carried opaquely —
    see `runtime/tipnav.py`."""

    view: object


@dataclass(frozen=True, slots=True)
class TipNavPopped:
    """Esc, or a back click. Whether there is anything to go back to is the reducer's answer."""


@dataclass(frozen=True, slots=True)
class TipNavCleared:
    """The tooltip went away, or moved to a new word — either way its back-history describes
    content nobody can navigate to any more."""


@dataclass(frozen=True, slots=True)
class CopyPulsed:
    """A copy asked for its "copied" border on `overlay`. `armed` is whether the expiry deadline
    took — a refusal the reducer decides against, not one the caller resolves on its own."""

    overlay: int
    armed: bool


@dataclass(frozen=True, slots=True)
class CopyPulseExpired:
    """The pulse's deadline landed."""


@dataclass(frozen=True, slots=True)
class HoverPauseClaimed:
    """A tooltip show ran the pause policy. `paused` is whether *this* show is what paused mpv."""

    paused: bool


@dataclass(frozen=True, slots=True)
class HoverPauseReleased:
    """The tooltip that may have paused playback is going away."""


@dataclass(frozen=True, slots=True)
class HoverWordResolved:
    """A lookup answered about the word now under the cursor. `meta` is opaque — see
    `runtime/hovered_word.py`. `revised` is the same answer arriving about the *same* word, which
    is the one case the kanji cycle must survive."""

    meta: object
    revised: bool = False


@dataclass(frozen=True, slots=True)
class HoverWordRead:
    """The shown panel named the hovered word's dictionary reading."""

    reading: str


@dataclass(frozen=True, slots=True)
class HoverWordForgotten:
    """Nothing is hovered, or the hover moved and its answer has not arrived."""


@dataclass(frozen=True, slots=True)
class HoverKanjiAdvanced:
    """`k` opened one of the hovered word's kanji."""


@dataclass(frozen=True, slots=True)
class PreviewShown:
    """A mined card's preview went up. Both payloads are opaque — see `runtime/card_preview.py`."""

    content: object
    audio: object = None


@dataclass(frozen=True, slots=True)
class PreviewDismissed:
    """✕, Esc, or a new cue took the preview down."""


@dataclass(frozen=True, slots=True)
class PreviewZoomToggled:
    """The screenshot was clicked."""


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
    | SidebarShown
    | SidebarHidden
    | SidebarReindexed
    | SidebarViewSelected
    | SidebarScrolled
    | SidebarFollowed
    | SidebarHoldReleased
    | TipNavPushed
    | TipNavPopped
    | TipNavCleared
    | CopyPulsed
    | CopyPulseExpired
    | HoverPauseClaimed
    | HoverPauseReleased
    | HoverWordResolved
    | HoverWordRead
    | HoverWordForgotten
    | HoverKanjiAdvanced
    | PreviewShown
    | PreviewDismissed
    | PreviewZoomToggled
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
    SidebarShown,
    SidebarHidden,
    SidebarReindexed,
    SidebarViewSelected,
    SidebarScrolled,
    SidebarFollowed,
    SidebarHoldReleased,
    TipNavPushed,
    TipNavPopped,
    TipNavCleared,
    CopyPulsed,
    CopyPulseExpired,
    HoverPauseClaimed,
    HoverPauseReleased,
    HoverWordResolved,
    HoverWordRead,
    HoverWordForgotten,
    HoverKanjiAdvanced,
    PreviewShown,
    PreviewDismissed,
    PreviewZoomToggled,
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
    | FileLoaded
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
