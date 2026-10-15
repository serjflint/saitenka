"""`Owner.SESSION`'s view of its transport: the reducer, the store's two paths, and what refuses.

The fact under test is a *sequenced* one — "the session has reached the loss in envelope order" —
which is why it is not the gateway's `ConnectionPhase` read through a property. The two disagree
for exactly as long as the mailbox holds observations published before the socket died, and that
window is the whole reason this state exists.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, bare_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.resources import Retiring, Starting
from saitenka.app.session.routes import (
    CUE_RETIRE_RESOURCE,
    SUBTITLE_REPLAY_PARTICIPANT,
    install_session_reactor,
)
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.connection import ConnectionState, ConnectionStore, reduce_connection
from saitenka.runtime.effects import ReplaySubtitleSelection, RetireCueIdentity
from saitenka.runtime.events import (
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EventEnvelope,
    EventOrigin,
    StartupReady,
)

LOST = ConnectionLost(1)
READY = ConnectionReady(2)
REPLACED = ConnectionReplaced(2)


@pytest.mark.parametrize(
    ("state", "event", "expected_state", "expected_effects"),
    [
        (ConnectionState(), LOST, ConnectionState(ready=False), (RetireCueIdentity(),)),
        (ConnectionState(ready=False), READY, ConnectionState(), ()),
        (ConnectionState(), READY, ConnectionState(), ()),
        (
            ConnectionState(),
            REPLACED,
            ConnectionState(),
            (ReplaySubtitleSelection(),),
        ),
        (ConnectionState(ready=False), StartupReady(), ConnectionState(ready=False), ()),
    ],
    ids=("lost", "ready", "already-ready", "replaced", "unrelated"),
)
def test_connection_transition_contract(state, event, expected_state, expected_effects) -> None:
    result = reduce_connection(state, event)
    assert (result.state, result.effects) == (expected_state, expected_effects)


def test_the_routed_and_unrouted_stores_agree_on_the_same_stream() -> None:
    """The differential. A store with a reactor reads the slot the mailbox already fed; one
    without reduces the events itself — and only the first is the path production takes."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
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
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    store = ConnectionStore(ipc)

    store.observed(LOST)

    assert store.current.ready is True, "the reactor owns the slot; nothing else may write it"
    gateway.close()


def test_a_failed_cue_retirement_ends_the_session_turn(make_session) -> None:
    """A reconnect cannot continue over a cue that its owner failed to retire."""
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
        options=ReaderOptions().with_overrides(prefetch=False),
    )

    def fail() -> None:
        raise RuntimeError("cue retirement failed")

    gateway.session_resources[CUE_RETIRE_RESOURCE] = Retiring(fail)
    try:
        assert gateway.publish_session_event(ConnectionLost(0))
        assert reader.pump() is False
    finally:
        reader.close()
        gateway.close()


def test_the_whole_connection_vocabulary_is_the_reactors() -> None:
    """All three are claimed, so a session with a reactor never hands one to the SessionController — and it
    must still end up ready and still have performed the acts.

    The census is the oracle rather than the state, because a claim that silently stopped reaching
    anything looks identical to a working one from the SessionController's side: nothing raises, the bit is
    right, and the reconnect simply never reaches the pipeline.
    """
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    store = ConnectionStore(ipc)
    replayed: list[bool] = []
    gateway.session_resources[SUBTITLE_REPLAY_PARTICIPANT] = Starting(lambda: replayed.append(True))

    # Ordered lost -> ready -> replaced, not lost -> replaced -> ready: handling a replacement
    # moves the reactor's epoch, and every later envelope is fenced against it. Production publishes
    # the whole replacement sequence at the new epoch, so this order is the one a stand-alone
    # publisher can produce without forging one.
    for event in (LOST, READY, REPLACED):
        gateway.publish_session_event(event)
    ipc.drain_events(0.0)

    assert store.current.ready is True
    assert replayed == [True]
    census = gateway.routing_census()
    assert {name: census[name] for name in ("ConnectionLost", "ConnectionReplaced")} == {
        "ConnectionLost": (1, 1),
        "ConnectionReplaced": (1, 1),
    }
    assert census["ConnectionReady"] == (1, 1)
    gateway.close()


def test_a_file_load_reaches_the_episode_owner_once(monkeypatch, make_session) -> None:
    from saitenka.runtime.events import FileLoaded

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reslotted: list[str] = []
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.start()
    monkeypatch.setattr(
        reader.graph.episode_watch,
        "file_loaded",
        lambda: reslotted.append("reslot"),
    )
    try:
        gateway.publish_session_event(FileLoaded())
        reader.pump()
    finally:
        reader.close()
        gateway.close()

    assert reslotted == ["reslot"]
