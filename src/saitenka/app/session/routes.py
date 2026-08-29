"""Route typed runtime events to their owning session features.

`owner_of` returns no owner for events outside the session graph. `OwnerRouter` records those
events in `gateway.session_ledger.counts` rather than treating them as dispatch errors.

It lives in `app/` rather than `mpvio/` because it names app features; `mpvio` must not import
`app`. The gateway only exposes the seam (`observe`, `mailbox`, `dispatch_effect`).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import saitenka.app.session.intents as session_intents
from saitenka.app import subtitle_intents
from saitenka.app.bindings import (
    ANALYSIS_MSG,
    ANNOTATION_MSG,
    BOOKMARK_MSG,
    CLICK_MSG,
    COPY_CLICK_MSG,
    COPY_LINE_MSG,
    COPY_MSG,
    HOVER_PAUSE_MSG,
    KANJI_MSG,
    LEGACY_RENDERER_MSG,
    MINE_ALL_MSG,
    MINE_MSG,
    MINE_VIDEO_MSG,
    OVERLAY_TOGGLE_MSG,
    PREVIEW_CLOSE_MSG,
    PREVIEW_MSG,
    PROFILE_CYCLE_MSG,
    SCROLL_DOWN_MSG,
    SCROLL_UP_MSG,
    SIDEBAR_MSG,
    SPEAK_MSG,
    SUB_ANCHOR_MSG,
    SUB_NEXT_MSG,
    SUB_PICKER_MSG,
    SUB_PREV_MSG,
    SUB_REPLAY_MSG,
    SUBTITLE_LANGUAGE_MSG,
    SUBTITLE_MARK_JP_MSG,
    SUBTITLE_RETRY_MSG,
    TIP_CLOSE_MSG,
    TIP_DOWN_MSG,
    TIP_UP_MSG,
    TRANS_MSG,
)
from saitenka.app.feature_bindings import (
    INTERACTION_OWNER_PLAN,
    INTERACTION_STATEFUL_BINDINGS,
    ordered_stateful_bindings,
)
from saitenka.app.features.mining import mine_intents
from saitenka.app.features.profiles import profile_intents
from saitenka.app.features.tooltip import hover_intents
from saitenka.app.session import interaction_intents, panel_intents
from saitenka.app.session.resources import ResourceRetirementError
from saitenka.app.session.stateless import StatelessCommandRegistration, bind_stateless
from saitenka.runtime.connection import ConnectionState, reduce_connection
from saitenka.runtime.diagnostics import RuntimeLedger
from saitenka.runtime.effects import (
    ApplyPlaybackDeltas,
    EffectOutcome,
    ExpireEffect,
    Owner,
    ReplaySubtitleSelection,
    RetireCueIdentity,
)
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
    StartupHintRequested,
    StartupReady,
    UserCommand,
)
from saitenka.runtime.interaction_slice import interaction_slice_reducer
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
from saitenka.runtime.startup_hint import StartupHintReducer, StartupHintState
from saitenka.runtime.state import RouteKey, SessionReducer, SessionState, SliceReducer
from saitenka.runtime.subtitle import SubtitleTrackState
from saitenka.runtime.subtitle_slice import SUBTITLE_FEATURE, subtitle_slice_reducer

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.mining.mine_adapter import MineCommandCoordinator
    from saitenka.app.features.profiles.profile_adapter import ProfileCommandEndpoint
    from saitenka.app.features.tooltip.hover_adapter import HoverCommandCoordinator
    from saitenka.app.session.adapter import SessionCommandCoordinator
    from saitenka.app.session.interaction_adapter import InteractionCommandCoordinator
    from saitenka.app.session.panel_adapter import PanelCommandCoordinator
    from saitenka.app.session.stateless import InstalledStatelessBinding
    from saitenka.app.subtitle_adapter import SubtitleCommandCoordinator
    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.effects import CoreControl, Effect
    from saitenka.runtime.events import RuntimeEvent
    from saitenka.runtime.state import FeatureReducer

log = logging.getLogger(__name__)

#: Events `Owner.SESSION` owns. Everything absent here routes to nobody and is counted.
_SESSION_EVENTS = (
    StartupHintRequested,
    StartupReady,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EffectFinished,
)

#: Owner-thread facts that share mailbox order with reactor events but have no reduced state.
_PASSTHROUGH = (FileLoaded, UserCommand)

#: Payload types the reactor claims rather than forwarding to the owner-thread turn.
#:
#: Declared, never derived from `_SESSION_EVENTS` — routing and claiming answer different
#: questions, and a payload joins this tuple only when the turn has no remaining part in it.
#:
#: Connection and property observations are fully owned by their stateful slices.
_CLAIMED = (
    StartupHintRequested,
    StartupReady,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    PropertyObserved,
)

#: Feature keys inside `Owner.SESSION`'s slice. Named once so a reader of the slot does not spell
#: a key itself and drift from the registration.
STARTUP_HINT = "startup-hint"
CONNECTION = "connection"
#: The cue identity a lost transport strands. A *retiring* act, so it goes in the close-verb table
#: with the rest — nothing about that seam is close-specific, and an act joins the runtime by
#: becoming an effect with a registered performer whatever the phase.
CUE_RETIRE_RESOURCE = "cue-identity-retire"
#: Applying what one reduced observation published. `Owner.PLAYBACK`'s outbox, delivered.
PLAYBACK_DELTAS_PERFORMER = "apply:playback-deltas"
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
_RESOURCE_OF: dict[type, tuple[str, ...]] = {RetireCueIdentity: (CUE_RETIRE_RESOURCE,)}


#: Which registered participant each setup effect brings up. Separate table from `_RESOURCE_OF`
#: because the verbs differ; sharing one would be the widening this vocabulary avoids.
_PARTICIPANT_OF: dict[type, str] = {
    ReplaySubtitleSelection: SUBTITLE_REPLAY_PARTICIPANT,
}


#: Which registered performer each act-on-a-payload effect reaches. The third table, because the
#: verb takes an argument: `start()` and `close()` answer for the session itself, and an act about
#: something the effect carries cannot be spelled as either.
_PERFORMER_OF: dict[type, str] = {
    ApplyPlaybackDeltas: PLAYBACK_DELTAS_PERFORMER,
}


def _perform(gateway: MpvGateway, name: str, effect: Effect) -> bool:
    """Hand one effect to its performer, or say it was never registered.

    Not isolated, like `_begin` and unlike `_retire`: a command that raises is a bug in the act, and
    the turn's own arm has never swallowed one either.
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


def _retire(
    gateway: MpvGateway,
    names: tuple[str, ...],
    *,
    missing_is_failure: bool = False,
) -> bool:
    """Close each named resource in order and report any failures after the sequence.

    The exception is delayed until every peer has run. The session turn converts it into a failed
    turn, preserving both isolation and caller-visible failure truth.
    """
    missing: list[str] = []
    failed: list[tuple[str, BaseException]] = []
    for name in names:
        resource = gateway.session_resources.get(name)
        if resource is None:
            missing.append(name)
            continue
        try:
            resource.close()  # type: ignore[attr-defined]  # registered by the owner that made it
        except BaseException as error:  # teardown continues; the owner's own close still ran
            log.warning("session resource %s failed to close", name, exc_info=True)
            failed.append((name, error))
    if failed or (missing and missing_is_failure):
        raise ResourceRetirementError(missing=tuple(missing), failed=tuple(failed))
    return not missing


def _dispatcher(gateway: MpvGateway) -> Callable[[Effect], bool]:
    """Perform an effect, app-side kinds first and the gateway's own kinds after.

    The composition lives here because `mpvio` must not import `app`: the gateway can perform an
    mpv command, and only this module knows that `DetachDiagnostics` means the app's telemetry.
    """

    def dispatch(effect: Effect) -> bool:
        performer = _PERFORMER_OF.get(type(effect))
        if performer is not None:
            return _perform(gateway, performer, effect)
        participant = _PARTICIPANT_OF.get(type(effect))
        if participant is not None:
            return _begin(gateway, participant)
        names = _RESOURCE_OF.get(type(effect))
        if names is not None:
            return _retire(
                gateway,
                names,
                missing_is_failure=not isinstance(effect, RetireCueIdentity),
            )
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
    from saitenka.mpvio.gateway import install_gateway

    gateway = install_gateway(ipc)
    install_session_reactor(gateway, startup_hint=startup_hint)
    return gateway


def install_session_reactor(gateway: MpvGateway, *, startup_hint: bool = True) -> SessionReactor:
    """Give the session a reactor that owns `Owner.SESSION`'s startup-hint slice, and start it.

    The reactor is installed whatever `startup_hint` says — it is the session's runtime, not the
    hint's. Only the seeding is optional: a screenshot capture must not carry the breadcrumb.

    The hint request is handed to the reactor directly rather than published, because it must
    reach mpv during the file-load window — before the live session drains the mailbox. `handle`
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
            CONNECTION: reduce_connection,
        }
    )
    playback = playback_slice_reducer()
    subtitle = subtitle_slice_reducer()
    interaction_bindings = ordered_stateful_bindings(
        INTERACTION_OWNER_PLAN,
        INTERACTION_STATEFUL_BINDINGS,
    )
    interaction = interaction_slice_reducer(
        {binding.key: binding.build_reducer() for binding in interaction_bindings}
    )
    presentation = presentation_slice_reducer()
    routes: dict[RouteKey, FeatureReducer] = {
        RouteKey(event, Owner.SESSION): session for event in _SESSION_EVENTS
    }
    # Raw observations are claimed at the gateway; the observation owner dispatches the other
    # playback declarations. Both paths meet in this slice, and the playback projection applies the
    # cross-feature consequences of its typed deltas.
    routes.update({RouteKey(event, Owner.PLAYBACK): playback for event in PLAYBACK_EVENTS})
    # `Owner.SUBTITLE` is not claimed either, and for a sharper reason: every event it takes is a
    # declaration of a track selection the sender has already sent to mpv. The slot holds what was
    # decided; the sending stays where it is until the duties move.
    routes.update({RouteKey(event, Owner.SUBTITLE): subtitle for event in SUBTITLE_EVENTS})
    # `Owner.INTERACTION` is not claimed for the third reason in the set: its events are
    # *observations*, so the reducer is what decides and the decisions come back through the
    # slice's outbox for the interaction owner to perform. The slot owns the hysteresis; the acts do not move
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
                    CONNECTION: ConnectionState(),
                }
            ),
            playback=playback.initial({PLAYBACK_FEATURE: PlaybackSlice()}),
            subtitle=subtitle.initial({SUBTITLE_FEATURE: SubtitleTrackState()}),
            interaction=interaction.initial(
                {binding.key: binding.initial() for binding in interaction_bindings}
            ),
            presentation=presentation.initial({PRESENTATION_FEATURE: TranslationState()}),
        ),
        OwnerRouter(
            SessionReducer(routes),
            owner_of,
            ledger=ledger,
            broadcast=LIFETIME_EVENTS,
            passthrough=_PASSTHROUGH,
        ),
        gateway.mailbox,
        _dispatcher(gateway),
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


def stateless_features(
    hover: HoverCommandCoordinator,
    mine: MineCommandCoordinator,
    panel: PanelCommandCoordinator,
    profile: ProfileCommandEndpoint,
    session: SessionCommandCoordinator,
    subtitle: SubtitleCommandCoordinator,
    interaction: InteractionCommandCoordinator,
) -> tuple[InstalledStatelessBinding, ...]:
    """The stateless half's route table — the counterpart to the reducer routes above.

    Same shape, different key: a stateful feature is reached by the event it owns, a stateless one
    by the command vocabulary it owns. Both are a registration, which is the point — neither half
    should require editing the host to add a feature.
    """
    return (
        bind_stateless("hover", hover_intents.HoverCommand, hover_intents.reduce, hover),
        bind_stateless("mine", mine_intents.MineCommand, mine_intents.reduce, mine),
        bind_stateless("panel", panel_intents.PanelCommand, panel_intents.reduce, panel),
        bind_stateless("profile", profile_intents.ProfileCommand, profile_intents.reduce, profile),
        bind_stateless("session", session_intents.SessionCommand, session_intents.reduce, session),
        bind_stateless(
            "subtitle", subtitle_intents.SubtitleCommand, subtitle_intents.reduce, subtitle
        ),
        bind_stateless(
            "interaction",
            interaction_intents.InteractionCommand,
            interaction_intents.reduce,
            interaction,
        ),
    )


STATELESS_COMMANDS = (
    StatelessCommandRegistration(
        SUBTITLE_LANGUAGE_MSG, subtitle_intents.SubtitleCommand.TOGGLE_LANGUAGE
    ),
    StatelessCommandRegistration(
        SUBTITLE_MARK_JP_MSG, subtitle_intents.SubtitleCommand.MARK_CURRENT_JAPANESE
    ),
    StatelessCommandRegistration(
        SUBTITLE_RETRY_MSG, subtitle_intents.SubtitleCommand.RETRY_ACQUISITION
    ),
    StatelessCommandRegistration(
        ANNOTATION_MSG, subtitle_intents.SubtitleCommand.TOGGLE_ANNOTATION_MODE
    ),
    StatelessCommandRegistration(TRANS_MSG, subtitle_intents.SubtitleCommand.TOGGLE_TRANSLATION),
    StatelessCommandRegistration(COPY_LINE_MSG, subtitle_intents.SubtitleCommand.COPY_LINE),
    StatelessCommandRegistration(SUB_PREV_MSG, subtitle_intents.SubtitleCommand.NAVIGATE_PREVIOUS),
    StatelessCommandRegistration(SUB_NEXT_MSG, subtitle_intents.SubtitleCommand.NAVIGATE_NEXT),
    StatelessCommandRegistration(SUB_REPLAY_MSG, subtitle_intents.SubtitleCommand.REPLAY_CUE),
    StatelessCommandRegistration(SUB_ANCHOR_MSG, subtitle_intents.SubtitleCommand.ANCHOR_TIMING),
    StatelessCommandRegistration(SPEAK_MSG, hover_intents.HoverCommand.SPEAK),
    StatelessCommandRegistration(COPY_MSG, hover_intents.HoverCommand.COPY),
    StatelessCommandRegistration(KANJI_MSG, hover_intents.HoverCommand.KANJI),
    StatelessCommandRegistration(HOVER_PAUSE_MSG, hover_intents.HoverCommand.TOGGLE_PAUSE),
    StatelessCommandRegistration(MINE_MSG, mine_intents.MineCommand.WORD),
    StatelessCommandRegistration(MINE_VIDEO_MSG, mine_intents.MineCommand.WORD_VIDEO),
    StatelessCommandRegistration(MINE_ALL_MSG, mine_intents.MineCommand.EPISODE),
    StatelessCommandRegistration(BOOKMARK_MSG, mine_intents.MineCommand.BOOKMARK_CUE),
    StatelessCommandRegistration(PROFILE_CYCLE_MSG, profile_intents.ProfileCommand.CYCLE),
    StatelessCommandRegistration(SIDEBAR_MSG, panel_intents.PanelCommand.TOGGLE_SIDEBAR),
    StatelessCommandRegistration(SUB_PICKER_MSG, panel_intents.PanelCommand.TOGGLE_SUBTITLE_PICKER),
    StatelessCommandRegistration(ANALYSIS_MSG, panel_intents.PanelCommand.TOGGLE_ANALYSIS),
    StatelessCommandRegistration(PREVIEW_MSG, panel_intents.PanelCommand.REPLAY_CARD_PREVIEW),
    StatelessCommandRegistration(PREVIEW_CLOSE_MSG, panel_intents.PanelCommand.CLOSE_CARD_PREVIEW),
    StatelessCommandRegistration(SCROLL_UP_MSG, interaction_intents.InteractionCommand.WHEEL_UP),
    StatelessCommandRegistration(
        SCROLL_DOWN_MSG, interaction_intents.InteractionCommand.WHEEL_DOWN
    ),
    StatelessCommandRegistration(TIP_UP_MSG, interaction_intents.InteractionCommand.TOOLTIP_UP),
    StatelessCommandRegistration(TIP_DOWN_MSG, interaction_intents.InteractionCommand.TOOLTIP_DOWN),
    StatelessCommandRegistration(
        TIP_CLOSE_MSG, interaction_intents.InteractionCommand.TOOLTIP_BACK_OR_CLOSE
    ),
    StatelessCommandRegistration(CLICK_MSG, interaction_intents.InteractionCommand.CLICK),
    StatelessCommandRegistration(
        COPY_CLICK_MSG, interaction_intents.InteractionCommand.COPY_UNDER_CURSOR
    ),
    StatelessCommandRegistration(OVERLAY_TOGGLE_MSG, session_intents.SessionCommand.TOGGLE_OVERLAY),
    StatelessCommandRegistration(
        LEGACY_RENDERER_MSG, session_intents.SessionCommand.TOGGLE_RENDERER
    ),
)
