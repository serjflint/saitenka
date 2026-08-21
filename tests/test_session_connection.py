"""`Owner.SESSION`'s view of its transport: the reducer, the store's two paths, and what refuses.

The fact under test is a *sequenced* one — "the session has reached the loss in envelope order" —
which is why it is not the gateway's `ConnectionPhase` read through a property. The two disagree
for exactly as long as the mailbox holds observations published before the socket died, and that
window is the whole reason this state exists.
"""

from __future__ import annotations

from util import FakeIPC, runtime_gateway

from saitenka.app.controller import Reader
from saitenka.app.session_routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.connection import ConnectionState, ConnectionStore, reduce_connection
from saitenka.runtime.events import (
    ConnectionLost,
    ConnectionReady,
    EventEnvelope,
    EventOrigin,
    SessionStarting,
)

LOST = ConnectionLost(1)
READY = ConnectionReady(2)


def test_a_session_starts_ready() -> None:
    """The default is not a refusal: a session is composed around a connection that is already up,
    and one that has heard nothing since has no reason to hold work back."""
    assert ConnectionState().ready is True


def test_losing_and_regaining_the_transport_is_one_bit_each_way() -> None:
    lost = reduce_connection(ConnectionState(), LOST)
    assert lost.state == ConnectionState(ready=False)
    assert reduce_connection(lost.state, READY).state == ConnectionState(ready=True)


def test_a_declaration_decides_nothing() -> None:
    """No outbox and no effects: by the time this is told, the socket is already gone or already
    back. What retiring a stranded cue identity means stays with the owner that holds one."""
    result = reduce_connection(ConnectionState(), LOST)
    assert result.effects == ()


def test_an_unrelated_session_event_leaves_the_connection_alone() -> None:
    """The slot is a slice, so every SESSION feature sees every SESSION event by broadcast."""
    assert reduce_connection(ConnectionState(ready=False), SessionStarting()).state == (
        ConnectionState(ready=False)
    )


def test_the_routed_and_unrouted_stores_agree_on_the_same_stream() -> None:
    """The differential. A store with a reactor reads the slot the mailbox already fed; one
    without reduces the events itself — and only the first is the path production takes."""
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reactor = install_session_reactor(gateway, startup_hint=False)
    routed = ConnectionStore(ipc)
    local = ConnectionStore(FakeIPC())  # no gateway, so no reactor to defer to

    for event in (LOST, READY, LOST):
        reactor.handle(EventEnvelope(0, 0.0, EventOrigin.LIFECYCLE, None, event))
        local.observed(event)

    assert routed.current == local.current == ConnectionState(ready=False)
    gateway.close()


def test_a_routed_store_does_not_reduce_what_the_reactor_already_did() -> None:
    """`observed` is the un-routed half. The mailbox delivers this event to the reactor before
    anything asks, so a store that also reduced it would apply the same fact twice — harmless for
    one bit, and the shape that is not harmless for a counter."""
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    store = ConnectionStore(ipc)

    store.observed(LOST)

    assert store.current.ready is True, "the reactor owns the slot; nothing else may write it"
    gateway.close()


def test_a_session_that_has_seen_the_transport_go_refuses_a_command() -> None:
    """The Reader-side consequence, driven through the drain rather than by setting the state:
    a command that reaches a session whose socket is gone is rejected, not queued at mpv."""
    from saitenka.app.runtime.commands import LegacyPickerRepeatGuard
    from saitenka.runtime.events import CommandOutcome, CommandReason, UserCommand

    ipc = FakeIPC()
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    guard = LegacyPickerRepeatGuard()
    try:
        reader._drain_event(LOST, guard)
        reader._drain_event(UserCommand("saitenka-help", command_id=7), guard)
    finally:
        reader.close()

    rejected = [o for o in ipc.runtime_outcomes if o.command_id == 7]
    assert [(o.outcome, o.reason) for o in rejected] == [
        (CommandOutcome.REJECTED, CommandReason.DISCONNECTED)
    ]
