"""`Owner.SESSION`'s view of its transport: the reducer, the store's two paths, and what refuses.

The fact under test is a *sequenced* one — "the session has reached the loss in envelope order" —
which is why it is not the gateway's `ConnectionPhase` read through a property. The two disagree
for exactly as long as the mailbox holds observations published before the socket died, and that
window is the whole reason this state exists.
"""

from __future__ import annotations

from session_builder import build_session
from util import FakeIPC, runtime_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.connection import ConnectionState, ConnectionStore, reduce_connection
from saitenka.runtime.effects import ReplaySubtitleSelection, RetireCueIdentity
from saitenka.runtime.events import (
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EventEnvelope,
    EventOrigin,
    SessionStarting,
)

LOST = ConnectionLost(1)
READY = ConnectionReady(2)
REPLACED = ConnectionReplaced(2)


def test_a_session_starts_ready() -> None:
    """The default is not a refusal: a session is composed around a connection that is already up,
    and one that has heard nothing since has no reason to hold work back."""
    assert ConnectionState().ready is True


def test_losing_and_regaining_the_transport_is_one_bit_each_way() -> None:
    lost = reduce_connection(ConnectionState(), LOST)
    assert lost.state == ConnectionState(ready=False)
    assert reduce_connection(lost.state, READY).state == ConnectionState(ready=True)


def test_the_acts_ride_out_as_effects_and_the_bit_stays_behind() -> None:
    """No outbox — an outbox hands a delta back for the caller to apply in its own frame, and
    neither of these acts is the caller's. `runtime` cannot name a tooltip or a subtitle track, so
    it decides only *that* a cue is stranded and *that* a replacement has never heard the
    selection; the performers are registered app-side."""
    assert reduce_connection(ConnectionState(), LOST).effects == (RetireCueIdentity(),)
    assert reduce_connection(ConnectionState(), REPLACED).effects == (ReplaySubtitleSelection(),)
    assert reduce_connection(ConnectionState(), READY).effects == (), (
        "learning the transport is back decides nothing but the bit"
    )


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
    """The SessionController-side consequence, driven through the drain rather than by setting the state:
    a command that reaches a session whose socket is gone is rejected, not queued at mpv."""
    from saitenka.runtime.events import CommandOutcome, CommandReason, UserCommand

    ipc = FakeIPC()
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    try:
        reader.turn._drain_event(LOST)
        reader.turn._drain_event(UserCommand("saitenka-help", command_id=7))
    finally:
        reader.close()

    rejected = [o for o in ipc.runtime_outcomes if o.command_id == 7]
    assert [(o.outcome, o.reason) for o in rejected] == [
        (CommandOutcome.REJECTED, CommandReason.DISCONNECTED)
    ]


def test_the_whole_connection_vocabulary_is_the_reactors() -> None:
    """All three are claimed, so a session with a reactor never hands one to the SessionController — and it
    must still end up ready and still have performed the acts.

    The census is the oracle rather than the state, because a claim that silently stopped reaching
    anything looks identical to a working one from the SessionController's side: nothing raises, the bit is
    right, and the reconnect simply never reaches the pipeline.
    """
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    store = ConnectionStore(ipc)

    # Ordered lost -> ready -> replaced, not lost -> replaced -> ready: handling a replacement
    # moves the reactor's epoch, and every later envelope is fenced against it. Production publishes
    # the whole replacement sequence at the new epoch, so this order is the one a stand-alone
    # publisher can produce without forging one.
    for event in (LOST, READY, REPLACED):
        gateway.publish_session_event(event)
    ipc.drain_events(0.0)

    assert store.current.ready is True
    census = gateway.claim_census()
    assert {name: census[name] for name in ("ConnectionLost", "ConnectionReplaced")} == {
        "ConnectionLost": (1, 1),
        "ConnectionReplaced": (1, 1),
    }
    assert census["ConnectionReady"] == (1, 1)
    gateway.close()


def test_a_file_load_reaches_the_reslot_through_an_effect(monkeypatch) -> None:
    """The episode boundary, claimed. The reducer holds nothing — whether this is a *new* file is
    the performer's question, answered against mpv when it acts — so the oracle is that the act
    happened, not that a field changed."""
    from saitenka.runtime.effects import ReslotEpisode
    from saitenka.runtime.episode import EpisodeBoundary, reduce_episode
    from saitenka.runtime.events import FileLoaded

    assert reduce_episode(EpisodeBoundary(), FileLoaded()).effects == (ReslotEpisode(),)
    assert reduce_episode(EpisodeBoundary(), READY).effects == (), (
        "the slice broadcasts, so every SESSION event reaches this and only one is its own"
    )

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reslotted: list[str] = []
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.start()
    monkeypatch.setattr(reader.turn, "_on_file_loaded", lambda: reslotted.append("reslot"))
    try:
        gateway.publish_session_event(FileLoaded())
        ipc.drain_events(0.0)
    finally:
        reader.close()
        gateway.close()

    assert reslotted == ["reslot"], (
        "claimed away from the SessionController, so the effect is the only path"
    )
    assert gateway.claim_census()["FileLoaded"] == (1, 1)
