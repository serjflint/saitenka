"""Wire the typed reactor into a live session, one migrated owner at a time.

This is the table D3 grows: a route per (event, owner) pair a feature actually owns, and an
`owner_of` that answers "nobody yet" for everything else. `OwnerRouter` turns that answer into a
counted fact instead of an error, so the gap is readable at any point in the migration
(`reactor_router.ignored`).

It lives in `app/` rather than `mpvio/` because it names app features; `mpvio` must not import
`app`. The gateway only exposes the seam (`observe`, `mailbox`, `dispatch_effect`).
"""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING

from saitenka.app import telemetry
from saitenka.app.lifecycle_close import LifecycleCloseState, reduce_lifecycle_close
from saitenka.app.startup_hint import StartupHintReducer, StartupHintState
from saitenka.runtime.effects import (
    CloseSessionOverlay,
    CloseSessionSurfaces,
    DetachDiagnostics,
    Owner,
    RemoveSessionArtifacts,
)
from saitenka.runtime.events import (
    ConnectionReplaced,
    EffectFinished,
    EventEnvelope,
    EventOrigin,
    SessionClosing,
    StartupHintRequested,
    StartupReady,
)
from saitenka.runtime.reactor import SessionReactor
from saitenka.runtime.routing import OwnerRouter
from saitenka.runtime.state import RouteKey, SessionReducer, SessionState, SliceReducer

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.effects import Effect
    from saitenka.runtime.events import RuntimeEvent
    from saitenka.runtime.state import FeatureReducer

#: Events `Owner.SESSION` owns. Everything absent here routes to nobody and is counted.
_SESSION_EVENTS = (
    StartupHintRequested,
    StartupReady,
    ConnectionReplaced,
    EffectFinished,
    SessionClosing,
)

#: Payload types the reactor handles *instead of* the legacy Reader, not merely as well as it.
#:
#: Declared, never derived from `_SESSION_EVENTS` — routing and claiming answer different
#: questions. `ConnectionReplaced` is routed here (the hint FSM resolves a lost acknowledgement on
#: it) yet must NOT be claimed: `Reader._on_ipc_reconnect` still drives
#: `subtitle_pipeline.connection_replaced`. Claim it and reconnects stop reaching the pipeline,
#: with nothing failing at the seam.
#:
#: A duty joins this tuple only when the Reader has no remaining part in it. That is the whole
#: migration protocol: add the route, move the state, then claim.
_CLAIMED = (StartupHintRequested, StartupReady, SessionClosing)

#: Feature keys inside `Owner.SESSION`'s slice. Named once so a reader of the slot does not spell
#: a key itself and drift from the registration.
STARTUP_HINT = "startup-hint"
LIFECYCLE_CLOSE = "lifecycle-close"

#: Names in `gateway.session_resources`. Spelled once for the same reason the feature keys are:
#: the owner that registers and the dispatcher that closes must not drift apart.
SURFACES_RESOURCE = "lifecycle-surfaces"
OVERLAY_RESOURCE = "overlay-transport"


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
    return None


def _dispatcher(gateway: MpvGateway) -> Callable[[Effect], bool]:
    """Perform an effect, app-side kinds first and the gateway's own kinds after.

    The composition lives here because `mpvio` must not import `app`: the gateway can perform an
    mpv command, and only this module knows that `DetachDiagnostics` means the app's telemetry.
    """

    def dispatch(effect: Effect) -> bool:
        if isinstance(effect, DetachDiagnostics):
            telemetry.set_gauge_provider(None)
            return True
        if isinstance(effect, CloseSessionSurfaces | CloseSessionOverlay):
            name = (
                SURFACES_RESOURCE if isinstance(effect, CloseSessionSurfaces) else OVERLAY_RESOURCE
            )
            resource = gateway.session_resources.get(name)
            # False, not an exception: an unregistered resource means this session's owner never
            # handed it over, so its own teardown still runs. The ledger records the difference.
            if resource is None:
                return False
            resource.close()  # type: ignore[attr-defined]  # registered by the owner that made it
            return True
        if isinstance(effect, RemoveSessionArtifacts):
            shutil.rmtree(effect.path, ignore_errors=True)
            return True
        return gateway.dispatch_effect(effect)

    return dispatch


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
    hint = StartupHintReducer(
        gateway.mailbox.allocate_effect,
        lambda: gateway.connection_epoch,
        time.monotonic,
    )
    # One slot, several features: `Owner.SESSION` is a slice from the start, so the second session
    # feature is a registration rather than a rewrite of the hint's reducer.
    session = SliceReducer({STARTUP_HINT: hint, LIFECYCLE_CLOSE: reduce_lifecycle_close})
    routes: dict[RouteKey, FeatureReducer] = {
        RouteKey(event, Owner.SESSION): session for event in _SESSION_EVENTS
    }
    reactor = SessionReactor(
        SessionState(
            session=session.initial(
                {STARTUP_HINT: StartupHintState(), LIFECYCLE_CLOSE: LifecycleCloseState()}
            ),
            playback=None,
            subtitle=None,
            interaction=None,
            presentation=None,
        ),
        OwnerRouter(SessionReducer(routes), owner_of),
        gateway.mailbox,
        _dispatcher(gateway),
    )

    def claims(payload: RuntimeEvent) -> bool:
        # A completion is claimed by *ownership*, not by type: the bridge and the reactor both
        # issue effects, and the bridge's terminals must keep reaching it or every correlated
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
