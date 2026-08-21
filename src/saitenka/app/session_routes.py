"""Wire the typed reactor into a live session, one migrated owner at a time.

This is the table D3 grows: a route per (event, owner) pair a feature actually owns, and an
`owner_of` that answers "nobody yet" for everything else. `OwnerRouter` turns that answer into a
counted fact instead of an error, so the gap is readable at any point in the migration —
`gateway.session_ledger.counts`, alongside what the session's reducers reported and controlled.

It lives in `app/` rather than `mpvio/` because it names app features; `mpvio` must not import
`app`. The gateway only exposes the seam (`observe`, `mailbox`, `dispatch_effect`).
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import TYPE_CHECKING, Protocol

from saitenka.app import interaction_intents, panel_intents, telemetry
from saitenka.app.interaction_adapter import InteractionAdapter, InteractionHost
from saitenka.app.panel_adapter import PanelAdapter, PanelHost
from saitenka.app.startup_hint import StartupHintReducer, StartupHintState
from saitenka.runtime.connection import ConnectionState, reduce_connection
from saitenka.runtime.diagnostics import RuntimeLedger
from saitenka.runtime.effects import (
    ApplyPlaybackDeltas,
    AttachSessionDiagnostics,
    CancelInteractionWork,
    CloseCapabilityActors,
    CloseSessionOverlay,
    CloseSessionStores,
    CloseSessionSurfaces,
    CloseSubtitleRendering,
    CloseWorkerLanes,
    DetachDiagnostics,
    EffectOutcome,
    EstablishRenderSpace,
    ExpireEffect,
    GuardMainRender,
    OpenSessionHistory,
    Owner,
    RegisterInputBindings,
    ReleaseInputCapture,
    RemoveSessionArtifacts,
    ReplaySubtitleSelection,
    ReslotEpisode,
    RetireCueIdentity,
    RunUserCommand,
    SeedOptionalCollaborators,
    StartPropertyObservation,
)
from saitenka.runtime.episode import EPISODE_FEATURE, EpisodeBoundary, reduce_episode
from saitenka.runtime.events import (
    INTERACTION_EVENTS,
    PLAYBACK_EVENTS,
    PRESENTATION_EVENTS,
    SUBTITLE_EVENTS,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EffectFinished,
    EpisodeRetired,
    EventEnvelope,
    EventOrigin,
    FileLoaded,
    PropertyObserved,
    SessionClosing,
    SessionStarting,
    StartupHintRequested,
    StartupReady,
    UserCommand,
)
from saitenka.runtime.interaction_slice import (
    HELP_FEATURE,
    HOVER_PAUSE_FEATURE,
    HOVERED_WORD_FEATURE,
    INTERACTION_FEATURE,
    PICKER_FEATURE,
    PREVIEW_FEATURE,
    PULSE_FEATURE,
    SIDEBAR_FEATURE,
    TIP_NAV_FEATURE,
    HelpFeature,
    HoveredWordFeature,
    HoverFeature,
    HoverPauseFeature,
    PickerFeature,
    PreviewFeature,
    PulseFeature,
    SidebarFeature,
    TipNavFeature,
    interaction_slice_reducer,
)
from saitenka.runtime.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close
from saitenka.runtime.lifecycle_start import LifecycleStartState, reduce_lifecycle_start
from saitenka.runtime.playback_slice import (
    PLAYBACK_FEATURE,
    PlaybackSlice,
    playback_slice_reducer,
)
from saitenka.runtime.presentation import TranslationState
from saitenka.runtime.presentation_slice import (
    PRESENTATION_FEATURE,
    presentation_slice_reducer,
)
from saitenka.runtime.reactor import SessionReactor
from saitenka.runtime.routing import OwnerRouter
from saitenka.runtime.state import RouteKey, SessionReducer, SessionState, SliceReducer
from saitenka.runtime.subtitle import SubtitleTrackState
from saitenka.runtime.subtitle_slice import SUBTITLE_FEATURE, subtitle_slice_reducer
from saitenka.runtime.user_command import COMMAND_FEATURE, CommandIntake, reduce_user_command

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.stateless import StatelessFeature
    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.effects import CoreControl, Effect
    from saitenka.runtime.events import RuntimeEvent
    from saitenka.runtime.state import FeatureReducer

log = logging.getLogger(__name__)

#: Events `Owner.SESSION` owns. Everything absent here routes to nobody and is counted.
_SESSION_EVENTS = (
    SessionStarting,
    StartupHintRequested,
    StartupReady,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    FileLoaded,
    UserCommand,
    EffectFinished,
    SessionClosing,
)

#: Payload types the reactor handles *instead of* the legacy Reader, not merely as well as it.
#:
#: Declared, never derived from `_SESSION_EVENTS` — routing and claiming answer different
#: questions, and a payload joins this tuple only when the Reader has no remaining part in it.
#:
#: What "no remaining part" means is the whole protocol: route the payload, move the act, then
#: claim. All three connection payloads are here because their acts moved — the stranded cue
#: identity and the track-selection replay are registered performers now, reached through an effect
#: rather than through a branch in the drain. `ConnectionReady` needed only the first two steps: it
#: decides nothing but the bit.
#:
#: `UserCommand` is here on the same protocol and is the one whose act carries a subject: the
#: binding table does not move, only the decision to consult it.
#:
#: `PropertyObserved` is the first claim that is not `Owner.SESSION`'s, and the first where the act
#: is *applying what the turn published*. It has to be claimed in the same breath as being routed:
#: the Reader's own `_reduce_playback` already routes through the reactor, so an observation both
#: routed and left to fall through would reduce twice.
#:
#: Claiming withholds from the Reader, so what is left in `_drain_event` for these is the
#: no-reactor fallback and nothing else. Deleting that would make a session without a runtime stop
#: noticing its transport, which is most of the unit suite.
_CLAIMED = (
    StartupHintRequested,
    StartupReady,
    SessionClosing,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    FileLoaded,
    UserCommand,
    PropertyObserved,
)

#: Feature keys inside `Owner.SESSION`'s slice. Named once so a reader of the slot does not spell
#: a key itself and drift from the registration.
STARTUP_HINT = "startup-hint"
LIFECYCLE_CLOSE = "lifecycle-close"
LIFECYCLE_START = "lifecycle-start"
CONNECTION = "connection"
EPISODE = EPISODE_FEATURE
COMMAND = COMMAND_FEATURE

#: Names in `gateway.session_resources`. Spelled once for the same reason the feature keys are:
#: the owner that registers and the dispatcher that closes must not drift apart.
SURFACES_RESOURCE = "lifecycle-surfaces"
OVERLAY_RESOURCE = "overlay-transport"
INPUT_CAPTURE_RESOURCE = "input-capture"
SESSION_SUMMARY_RESOURCE = "session-summary"
BACKLOG_RESOURCE = "backlog-store"
MINED_RESOURCE = "mined-store"
SUBTITLE_DEACTIVATE_RESOURCE = "subtitle-deactivate"
SUBTITLE_CLEAR_RESOURCE = "subtitle-clear"
SUBTITLE_CLOSE_RESOURCE = "subtitle-close"
#: The cue identity a lost transport strands. A *retiring* act, so it goes in the close-verb table
#: with the rest — nothing about that seam is close-specific, and an act moves off the Reader by
#: becoming an effect with a registered performer whatever the phase.
CUE_RETIRE_RESOURCE = "cue-identity-retire"
#: Re-slotting onto a newly loaded file. A *starting* act — the episode is being established — so it
#: goes in the start table beside the subtitle replay.
RESLOT_PARTICIPANT = "start:episode-reslot"
#: Running one arrived command. Neither verb fits: it starts nothing and retires nothing, and it is
#: the only act so far whose effect has to say what it is about.
COMMAND_PERFORMER = "run:user-command"
#: Applying what one reduced observation published. `Owner.PLAYBACK`'s outbox, delivered.
PLAYBACK_DELTAS_PERFORMER = "apply:playback-deltas"

#: The optional collaborators' probes, and the interaction work that outlives a cancelled hover.
CAPABILITY_PARTICIPANTS = ("capability:tts", "capability:anki")
INTERACTION_WORK_PARTICIPANTS = ("interaction-jobs", "hover-metadata")

#: Every worker and job lane, in the one order that is safe. Declared here rather than derived from
#: the lane registry: the registry knows the lanes, not that the geometry executor must stop before
#: the state it renders against, nor that the atlas is uninstalled only after its lane has drained.
#:
#: The trailing four have no ordering constraint at all — whatever owns their state closed in an
#: earlier phase — so they go last, where they cannot spend the shared lane budget the constrained
#: chain above them needs. A lane closes by *cancelling* first and joining second, so a starved
#: budget still stops the work; it only stops waiting for it.
WORKER_LANE_PARTICIPANTS = (
    "lanes:stop-workers",
    "lanes:subtitle-fetch",
    "lanes:subtitle-picker",
    "lanes:geometry",
    "lanes:annotation",
    "lanes:cue-annotation",
    "lanes:tooltip-raster",
    "lanes:tooltip-render-ahead",
    "lanes:tooltip-engaged-worker",
    "lanes:tooltip-engaged",
    "lanes:prefetch",
    "lanes:speculative-prefetch",
    "lanes:mask-atlas-startup-worker",
    "lanes:mask-atlas-startup",
    "lanes:mask-atlas-uninstall",
    "lanes:capabilities",
    "lanes:interaction-metadata",
    "lanes:mined-seed",
    "lanes:episode-analysis",
)

#: Setup participants. Prefixed because the two halves are separate contracts, not two uses of one:
#: a startup participant answers `start()` and a close participant answers `close()`.
RENDER_GUARD_PARTICIPANT = "start:render-guard"
RENDER_SPACE_PARTICIPANT = "start:render-space"
OBSERVERS_PARTICIPANT = "start:observers"
INPUT_PARTICIPANT = "start:input"
COLLABORATORS_PARTICIPANT = "start:collaborators"
HISTORY_PARTICIPANT = "start:history"
DIAGNOSTICS_PARTICIPANT = "start:diagnostics"
#: Re-asserting the track selection against a replacement connection. In the *start* table, not the
#: close one: the connection that has never heard the selection needs it established, and a verb
#: that meant both would be a lie in the one place this vocabulary has to stay readable.
SUBTITLE_REPLAY_PARTICIPANT = "start:subtitle-replay"


#: Events that belong to no single owner and are reduced by every slice that registers them.
LIFETIME_EVENTS = (EpisodeRetired,)


def owner_of(event: RuntimeEvent) -> Owner | None:
    """Which owner an event belongs to, or None while nothing owns it.

    A completion belongs to whoever issued the effect — it carries its owner, and the reactor has
    already refused any completion it did not dispatch, so a bridge-owned SESSION effect never
    reaches a reducer.
    """
    if isinstance(event, EffectFinished):
        return event.owner
    if isinstance(event, _SESSION_EVENTS):
        return Owner.SESSION
    if isinstance(event, PLAYBACK_EVENTS):
        return Owner.PLAYBACK
    if isinstance(event, SUBTITLE_EVENTS):
        return Owner.SUBTITLE
    if isinstance(event, INTERACTION_EVENTS):
        return Owner.INTERACTION
    if isinstance(event, PRESENTATION_EVENTS):
        return Owner.PRESENTATION
    return None


#: Which registered resource each retiring effect closes. A table rather than a chain of
#: `isinstance`, so adding a duty is a row and cannot pick the wrong branch by falling through.
#: The names are a tuple because a duty can have several participants that retire together; the
#: order inside one is the contract, exactly as it is between two effects in one phase.
_RESOURCE_OF: dict[type, tuple[str, ...]] = {
    RetireCueIdentity: (CUE_RETIRE_RESOURCE,),
    CloseSessionSurfaces: (SURFACES_RESOURCE,),
    CloseSessionOverlay: (OVERLAY_RESOURCE,),
    ReleaseInputCapture: (INPUT_CAPTURE_RESOURCE,),
    CloseCapabilityActors: CAPABILITY_PARTICIPANTS,
    CancelInteractionWork: INTERACTION_WORK_PARTICIPANTS,
    CloseWorkerLanes: WORKER_LANE_PARTICIPANTS,
    CloseSessionStores: (SESSION_SUMMARY_RESOURCE, BACKLOG_RESOURCE, MINED_RESOURCE),
    CloseSubtitleRendering: (
        SUBTITLE_DEACTIVATE_RESOURCE,
        SUBTITLE_CLEAR_RESOURCE,
        SUBTITLE_CLOSE_RESOURCE,
    ),
}


#: Which registered participant each setup effect brings up. Separate table from `_RESOURCE_OF`
#: because the verbs differ; sharing one would be the widening this vocabulary avoids.
_PARTICIPANT_OF: dict[type, str] = {
    ReplaySubtitleSelection: SUBTITLE_REPLAY_PARTICIPANT,
    ReslotEpisode: RESLOT_PARTICIPANT,
    GuardMainRender: RENDER_GUARD_PARTICIPANT,
    EstablishRenderSpace: RENDER_SPACE_PARTICIPANT,
    StartPropertyObservation: OBSERVERS_PARTICIPANT,
    RegisterInputBindings: INPUT_PARTICIPANT,
    SeedOptionalCollaborators: COLLABORATORS_PARTICIPANT,
    OpenSessionHistory: HISTORY_PARTICIPANT,
    AttachSessionDiagnostics: DIAGNOSTICS_PARTICIPANT,
}


#: Which registered performer each act-on-a-payload effect reaches. The third table, because the
#: verb takes an argument: `start()` and `close()` answer for the session itself, and an act about
#: something the effect carries cannot be spelled as either.
_PERFORMER_OF: dict[type, str] = {
    RunUserCommand: COMMAND_PERFORMER,
    ApplyPlaybackDeltas: PLAYBACK_DELTAS_PERFORMER,
}


def _perform(gateway: MpvGateway, name: str, effect: Effect) -> bool:
    """Hand one effect to its performer, or say it was never registered.

    Not isolated, like `_begin` and unlike `_retire`: a command that raises is a bug in the act, and
    the Reader's own arm has never swallowed one either.
    """
    performer = gateway.session_resources.get(name)
    if performer is None:
        return False
    performer.perform(effect)  # type: ignore[attr-defined]  # registered by the owner that made it
    return True


def _begin(gateway: MpvGateway, name: str) -> bool:
    """Run one setup participant, or say it was never registered.

    Not isolated, unlike `_retire`: a setup step that fails has not left the session half-torn-down
    but half-*built*, and the phases behind it depend on it. Teardown must continue at all costs;
    setup must not pretend it happened.
    """
    participant = gateway.session_resources.get(name)
    if participant is None:
        return False
    participant.start()  # type: ignore[attr-defined]  # registered by the owner that made it
    return True


def _retire(gateway: MpvGateway, names: tuple[str, ...]) -> bool:
    """Close each named resource in order, isolating them, and say whether all of them were there.

    False, not an exception, for an unregistered one: it means this session's owner never handed
    it over, so its own teardown still runs. Isolation is what `CloseLedger` gives a step it owns —
    a phase that retires three participants must not lose it by being one announcement.
    """
    retired = True
    for name in names:
        resource = gateway.session_resources.get(name)
        if resource is None:
            retired = False
            continue
        try:
            resource.close()  # type: ignore[attr-defined]  # registered by the owner that made it
        except Exception:  # teardown continues; the owner's own close still ran
            log.warning("session resource %s failed to close", name, exc_info=True)
            retired = False
    return retired


def _dispatcher(gateway: MpvGateway, ledger: RuntimeLedger) -> Callable[[Effect], bool]:
    """Perform an effect, app-side kinds first and the gateway's own kinds after.

    The composition lives here because `mpvio` must not import `app`: the gateway can perform an
    mpv command, and only this module knows that `DetachDiagnostics` means the app's telemetry.
    """

    def dispatch(effect: Effect) -> bool:
        if isinstance(effect, DetachDiagnostics):
            telemetry.set_gauge_provider(None)
            # The session's last chance to say what it routed and what it dropped. Detaching
            # diagnostics is where the session stops being observable, so the census goes out
            # here or nowhere.
            log.debug("runtime census: %s", ledger.counts)
            return True
        performer = _PERFORMER_OF.get(type(effect))
        if performer is not None:
            return _perform(gateway, performer, effect)
        participant = _PARTICIPANT_OF.get(type(effect))
        if participant is not None:
            return _begin(gateway, participant)
        names = _RESOURCE_OF.get(type(effect))
        if names is not None:
            return _retire(gateway, names)
        if isinstance(effect, RemoveSessionArtifacts):
            shutil.rmtree(effect.path, ignore_errors=True)
            return True
        return gateway.dispatch_effect(effect)

    return dispatch


class ControlSink:
    """Perform a cancel/expire. Without this sink the reactor drops both, silently.

    The two kinds have different owners. `ExpireEffect` is the gateway's: it holds the in-flight
    request, so only it can turn a passed deadline into a TIMEOUT terminal. `CancelEffect` is the
    reactor's own — publishing the CANCELLED completion is what lets the issuing reducer see its
    effect end, and the `owns` guard is the one `_finish` already applies: never retire an effect
    this reactor did not dispatch.

    A class rather than a closure because the reactor does not exist until after the sink is passed
    to it. The binding is late either way; this makes it a visible assignment instead of a cell.
    """

    def __init__(self, gateway: MpvGateway, ledger: RuntimeLedger) -> None:
        self._gateway = gateway
        self._ledger = ledger
        self.reactor: SessionReactor | None = None

    def __call__(self, control: CoreControl) -> None:
        if isinstance(control, ExpireEffect):
            self._ledger.control("expire")
            self._gateway.expire(control)
            return
        self._ledger.control(f"cancel:{control.owner.value}")
        if self.reactor is None or not self.reactor.owns(control.target_effect_id):
            return
        self.reactor.complete(
            EffectFinished(
                control.target_effect_id,
                control.owner,
                control.identity,
                EffectOutcome.CANCELLED,
            ),
            origin=EventOrigin.LIFECYCLE,
        )


def install_session_runtime(ipc: MpvIPC, *, startup_hint: bool = True) -> MpvGateway:
    """Wire one live mpv connection into a full session runtime — gateway *and* reactor.

    Two calls, one decision: a gateway without a reactor is a session whose `Owner.SESSION`
    duties never run, which is what `attach` silently was. Entrypoints ask for a session runtime,
    not for the two halves in the right order.
    """
    from saitenka.mpvio.gateway import install_legacy_gateway

    gateway = install_legacy_gateway(ipc)
    install_session_reactor(gateway, startup_hint=startup_hint)
    return gateway


def install_session_reactor(gateway: MpvGateway, *, startup_hint: bool = True) -> SessionReactor:
    """Give the session a reactor that owns `Owner.SESSION`'s startup-hint slice, and start it.

    The reactor is installed whatever `startup_hint` says — it is the session's runtime, not the
    hint's. Only the seeding is optional: a screenshot capture must not carry the breadcrumb.

    The hint request is handed to the reactor directly rather than published, because it must
    reach mpv during the file-load window — before a Reader exists to drain the mailbox. `handle`
    takes an envelope and reads nothing else, so constructing one here is the whole cost; the
    sequence number is the mailbox's ordering device and unused by the reactor.
    """
    # Only two collaborators, and neither decides: an effect-ID allocator and a clock, both of
    # which stamp an effect the turn has already chosen. The connection epoch used to be a third and
    # was the one that branched — it is slice state now, fed by the connection payloads routed here.
    hint = StartupHintReducer(gateway.mailbox.allocate_effect, time.monotonic)
    # One slot, several features: `Owner.SESSION` is a slice from the start, so the second session
    # feature is a registration rather than a rewrite of the hint's reducer.
    session = SliceReducer(
        {
            STARTUP_HINT: hint,
            LIFECYCLE_CLOSE: reduce_lifecycle_close,
            LIFECYCLE_START: reduce_lifecycle_start,
            CONNECTION: reduce_connection,
            EPISODE: reduce_episode,
            COMMAND: reduce_user_command,
        }
    )
    playback = playback_slice_reducer()
    subtitle = subtitle_slice_reducer()
    interaction = interaction_slice_reducer()
    presentation = presentation_slice_reducer()
    routes: dict[RouteKey, FeatureReducer] = {
        RouteKey(event, Owner.SESSION): session for event in _SESSION_EVENTS
    }
    # `Owner.PLAYBACK` is not claimed: the Reader routes its observations here and then acts on
    # the deltas the turn published. What has moved is the *state* — the slot is where the
    # projection lives, so there is one of it. The duties that read it move next.
    routes.update({RouteKey(event, Owner.PLAYBACK): playback for event in PLAYBACK_EVENTS})
    # `Owner.SUBTITLE` is not claimed either, and for a sharper reason: every event it takes is a
    # declaration of a track selection the sender has already sent to mpv. The slot holds what was
    # decided; the sending stays where it is until the duties move.
    routes.update({RouteKey(event, Owner.SUBTITLE): subtitle for event in SUBTITLE_EVENTS})
    # `Owner.INTERACTION` is not claimed for the third reason in the set: its events are
    # *observations*, so the reducer is what decides and the decisions come back through the
    # slice's outbox for the Reader to perform. The slot owns the hysteresis; the acts do not move
    # until the tooltip's own state does.
    routes.update({RouteKey(event, Owner.INTERACTION): interaction for event in INTERACTION_EVENTS})
    # `Owner.PRESENTATION` is a declaring slice like SUBTITLE's: the sender has already drawn or
    # removed the surface. The slot holds what is on screen and who is holding it up.
    routes.update(
        {RouteKey(event, Owner.PRESENTATION): presentation for event in PRESENTATION_EVENTS}
    )
    # The one event that is nobody's: an episode ending retires each owner's per-episode facts in
    # a single turn. Registered per owner that has any — SESSION has none, and its absence from
    # this list is the answer rather than a gap.
    for owner, reducer in (
        (Owner.PLAYBACK, playback),
        (Owner.SUBTITLE, subtitle),
        (Owner.INTERACTION, interaction),
        (Owner.PRESENTATION, presentation),
    ):
        routes[RouteKey(EpisodeRetired, owner)] = reducer
    ledger = RuntimeLedger()
    gateway.session_ledger = ledger
    control = ControlSink(gateway, ledger)
    reactor = SessionReactor(
        SessionState(
            session=session.initial(
                {
                    STARTUP_HINT: StartupHintState(),
                    LIFECYCLE_CLOSE: LifecycleCloseState(),
                    LIFECYCLE_START: LifecycleStartState(),
                    CONNECTION: ConnectionState(),
                    EPISODE: EpisodeBoundary(),
                    COMMAND: CommandIntake(),
                }
            ),
            playback=playback.initial({PLAYBACK_FEATURE: PlaybackSlice()}),
            subtitle=subtitle.initial({SUBTITLE_FEATURE: SubtitleTrackState()}),
            interaction=interaction.initial(
                {
                    INTERACTION_FEATURE: HoverFeature(),
                    HELP_FEATURE: HelpFeature(),
                    PICKER_FEATURE: PickerFeature(),
                    SIDEBAR_FEATURE: SidebarFeature(),
                    TIP_NAV_FEATURE: TipNavFeature(),
                    PULSE_FEATURE: PulseFeature(),
                    HOVER_PAUSE_FEATURE: HoverPauseFeature(),
                    HOVERED_WORD_FEATURE: HoveredWordFeature(),
                    PREVIEW_FEATURE: PreviewFeature(),
                }
            ),
            presentation=presentation.initial({PRESENTATION_FEATURE: TranslationState()}),
        ),
        OwnerRouter(SessionReducer(routes), owner_of, ledger=ledger, broadcast=LIFETIME_EVENTS),
        gateway.mailbox,
        _dispatcher(gateway, ledger),
        diagnostics=ledger.diagnostic,
        control=control,
    )
    control.reactor = reactor
    # Attaching is part of installing, not a step the caller adds: every port that reaches the
    # session goes through `gateway.session_reactor`, so a reactor that runs but is not findable
    # is a session whose owners silently fall back to whatever the caller kept locally.
    gateway.session_reactor = reactor

    def claims(payload: RuntimeEvent) -> bool:
        # A completion is claimed by *ownership*, not by type: the correlator and the reactor both
        # issue effects, and the correlator's terminals must keep reaching it or every correlated
        # command it owns hangs.
        if isinstance(payload, EffectFinished):
            return reactor.owns(payload.effect_id)
        return isinstance(payload, _CLAIMED)

    gateway.observe(reactor, claims)
    if startup_hint:
        reactor.handle(
            EventEnvelope(
                0,
                time.monotonic(),
                EventOrigin.LIFECYCLE,
                gateway.connection_epoch,
                StartupHintRequested(),
            )
        )
    return reactor


class StatelessHost(InteractionHost, PanelHost, Protocol):
    """Every stateless feature's host surface at once, one base class per registered feature.

    There is no intersection type, so this is how the composition root types the object it hands
    each adapter. Adding a feature adds a base here — which is the registration cost being visible
    rather than hidden behind a `Reader` annotation.
    """


def stateless_features(host: StatelessHost) -> dict[type, StatelessFeature]:
    """The stateless half's route table — the counterpart to the reducer routes above.

    Same shape, different key: a stateful feature is reached by the event it owns, a stateless one
    by the command vocabulary it owns. Both are a registration, which is the point — neither half
    should require editing the host to add a feature.
    """
    return {
        panel_intents.PanelCommand: (panel_intents.reduce, PanelAdapter(host)),
        interaction_intents.InteractionCommand: (
            interaction_intents.reduce,
            InteractionAdapter(host),
        ),
    }
