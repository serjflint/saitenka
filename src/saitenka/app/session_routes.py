"""Wire the typed reactor into a live session, one migrated owner at a time.

This is the table D3 grows: a route per (event, owner) pair a feature actually owns, and an
`owner_of` that answers "nobody yet" for everything else. `OwnerRouter` turns that answer into a
counted fact instead of an error, so the gap is readable at any point in the migration
(`reactor_router.ignored`).

It lives in `app/` rather than `mpvio/` because it names app features; `mpvio` must not import
`app`. The gateway only exposes the seam (`observe`, `mailbox`, `dispatch_effect`).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from saitenka.app.startup_hint import StartupHintReducer, StartupHintState
from saitenka.runtime.effects import Owner
from saitenka.runtime.events import (
    ConnectionReplaced,
    EffectFinished,
    EventEnvelope,
    EventOrigin,
    StartupHintRequested,
    StartupReady,
)
from saitenka.runtime.reactor import SessionReactor
from saitenka.runtime.routing import OwnerRouter
from saitenka.runtime.state import RouteKey, SessionReducer, SessionState

if TYPE_CHECKING:
    from saitenka.mpvio.gateway import MpvGateway
    from saitenka.runtime.events import RuntimeEvent
    from saitenka.runtime.state import FeatureReducer

#: Events `Owner.SESSION` owns. Everything absent here routes to nobody and is counted.
_SESSION_EVENTS = (StartupHintRequested, StartupReady, ConnectionReplaced, EffectFinished)


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
    routes: dict[RouteKey, FeatureReducer] = {
        RouteKey(event, Owner.SESSION): hint for event in _SESSION_EVENTS
    }
    reactor = SessionReactor(
        SessionState(
            session=StartupHintState(),
            playback=None,
            subtitle=None,
            interaction=None,
            presentation=None,
        ),
        OwnerRouter(SessionReducer(routes), owner_of),
        gateway.mailbox,
        gateway.dispatch_effect,
    )
    gateway.observe(reactor)
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
