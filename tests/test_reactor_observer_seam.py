"""The seam two reactor implementations share while one migrates into the other.

`SessionReactor` (typed) now owns a slice of the session; `LegacyRuntimeBridge` (callback-shaped)
still owns the rest. The seam has two halves, and the tests here pin both:

* **observe** — the reactor sees every envelope, so it can track epochs and its own completions.
* **claim** — a payload it owns is withheld from the legacy Reader, so a migrated duty runs once
  instead of twice. Claiming is *declared*, never derived from the route table: the two answer
  different questions, and conflating them is silent.

Every hazard asserted here was found by executing the code, not by reading it.

**Most of this file is scaffolding with a known demolition date.** Everything marked `TRANSITIONAL`
exists only while two reactor implementations coexist, and D4 — which deletes `LegacyRuntimeBridge`
and `LegacyEventRouter` — deletes it too. It is written down so that removal is a decision someone
can make quickly, rather than a judgement call they avoid. The `OwnerRouter` test below is the one
that outlives them.
"""

from __future__ import annotations

from util import FakeIPC

from saitenka.runtime import (
    EffectFinished,
    EffectOutcome,
    EventOrigin,
    Owner,
    RawMpvEvent,
    SessionMailbox,
    TrafficClass,
)
from saitenka.runtime.effects import EffectId
from saitenka.runtime.reactor import SessionReactor
from saitenka.runtime.routing import OwnerRouter
from saitenka.runtime.state import (
    ReduceResult,
    RouteKey,
    SessionReducer,
    SessionState,
)


def _state() -> SessionState:
    return SessionState(session=(), playback=(), subtitle=(), interaction=(), presentation=())


def _reactor(mailbox, router):
    return SessionReactor(_state(), router, mailbox, lambda _effect: True)


def test_an_unrouted_event_is_ignored_and_counted_not_raised() -> None:
    """`RouteError` subclasses `ValueError`, and `Reader.pump` reads `ValueError` as "mpv went away".

    So letting one escape would end the session silently the first time an unmigrated event arrived
    — which, during a migration, is most of them.
    """
    router = OwnerRouter(SessionReducer({}), lambda _event: Owner.PLAYBACK)
    state = _state()

    result_state, effects = router(
        state, RawMpvEvent("property-change", {"event": "property-change"})
    )

    assert result_state is state
    assert effects == ()
    assert router.ignored == {"playback:RawMpvEvent": 1}


def test_a_claimed_event_is_withheld_from_the_reader() -> None:
    """The fallthrough seam: a migrated duty runs in the reactor *instead of* the Reader.

    Without the claim both act on the same envelope and the duty runs twice.
    """
    from saitenka.mpvio.gateway import LegacyEventRouter

    mailbox = SessionMailbox()
    consumer = LegacyEventRouter(mailbox)
    consumer.observe(
        _reactor(mailbox, OwnerRouter(SessionReducer({}), lambda _e: None)),
        lambda payload: isinstance(payload, RawMpvEvent) and payload.name == "claimed",
    )
    for name in ("claimed", "unclaimed"):
        mailbox.publish(
            RawMpvEvent(name, {"event": name}),
            origin=EventOrigin.MPV,
            traffic=TrafficClass.NORMAL,
        )

    assert consumer.drain_events() == [{"event": "unclaimed"}]


def test_a_routed_event_the_reader_still_needs_is_not_claimed() -> None:
    """`ConnectionReplaced` is routed to the startup-hint reducer AND drives
    `subtitle_pipeline.connection_replaced`.

    Deriving the claim set from the route table would swallow it, and nothing fails at the seam —
    reconnects just stop reaching the pipeline. This pins the two apart.
    """
    from saitenka.app.session_routes import _CLAIMED, _SESSION_EVENTS, owner_of
    from saitenka.runtime.events import ConnectionReplaced

    assert ConnectionReplaced in _SESSION_EVENTS  # routed
    assert ConnectionReplaced not in _CLAIMED  # but never claimed
    assert owner_of(ConnectionReplaced(1)) is Owner.SESSION


def test_a_bridge_owned_completion_is_never_claimed_by_the_reactor() -> None:
    """Claiming a completion is an ownership question, not a type question.

    The bridge and the reactor both issue effects. Claiming by type would strand every correlated
    command the bridge owns — its terminal would be withheld and its callback never run.
    """
    from util import runtime_gateway

    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reactor = install_session_reactor(gateway)
    try:
        bridge_effect = gateway.mailbox.allocate_effect()
        assert reactor.owns(bridge_effect) is False
    finally:
        gateway.close()


def test_effect_ids_from_one_mailbox_never_collide_across_allocators() -> None:
    """D2: the mailbox owns the ID because the mailbox is what the ID must be unique within.

    Before this, `LegacyRuntimeBridge` counted from 0 privately. A second allocator — which is
    exactly what D3 makes the reactor — would have reissued IDs the bridge still held, and the
    only symptom is `reserve_terminal` returning False, which every caller already treats as an
    overloaded lane.
    """
    mailbox = SessionMailbox()
    bridge_ids = [mailbox.allocate_effect() for _ in range(3)]
    reactor_ids = mailbox.allocate_effects(3)

    assert set(bridge_ids).isdisjoint(reactor_ids)
    # Every one is reservable: a collision would surface here as a False, not as an exception.
    assert all(mailbox.reserve_terminal(effect_id) for effect_id in (*bridge_ids, *reactor_ids))


def test_the_observer_never_retires_a_terminal_the_bridge_owns() -> None:
    """The hazard that silently breaks every correlated command.

    Measured before `_finish` checked ownership: the reactor retired a completion it never
    dispatched, the bridge's own `retire_terminal` then returned False, and it dropped the
    completion — so whatever awaited that command waited forever.

    Outlives D4 in meaning, not in form: the *rule* (retiring is a claim of ownership) is
    permanent; only the second owner here is temporary.
    """
    mailbox = SessionMailbox()
    effect_id = EffectId(7)
    assert mailbox.reserve_terminal(effect_id)  # the bridge owns this effect

    from saitenka.mpvio.gateway import LegacyEventRouter

    consumer = LegacyEventRouter(mailbox)
    consumer.observe(_reactor(mailbox, OwnerRouter(SessionReducer({}), lambda _e: None)))

    mailbox.publish_terminal(
        EffectFinished(effect_id, Owner.PLAYBACK, "identity", EffectOutcome.SUCCEEDED),
        origin=EventOrigin.MPV,
    )
    consumer.drain_events(ordered_terminals=True)

    assert mailbox.retire_terminal(effect_id) is True  # still the bridge's to complete


def test_the_observer_sees_domain_events_without_consuming_them() -> None:
    """TRANSITIONAL (dies with D4). A second observer must cost the Reader nothing: `handle` takes an envelope, it does not read
    the mailbox. (`run_until_idle` does, and must not be used while this router exists.)"""
    mailbox = SessionMailbox()
    seen: list[object] = []
    router = OwnerRouter(
        SessionReducer(
            {
                RouteKey(RawMpvEvent, Owner.PLAYBACK): lambda s, e: (
                    seen.append(e),
                    ReduceResult(s),
                )[1]
            }
        ),
        lambda _event: Owner.PLAYBACK,
    )

    from saitenka.mpvio.gateway import LegacyEventRouter

    consumer = LegacyEventRouter(mailbox)
    consumer.observe(_reactor(mailbox, router))

    mailbox.publish(
        RawMpvEvent("property-change", {"event": "property-change", "name": "pause"}),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    events = consumer.drain_events()

    assert len(seen) == 1  # the reactor saw it
    assert events == [{"event": "property-change", "name": "pause"}]  # and so did the Reader


def test_a_session_with_an_observer_issues_the_same_ipc_as_one_without() -> None:
    """TRANSITIONAL (dies with D4). D1's exit criterion: observing changes nothing mpv can notice.

    Achievable only because terminals are fenced — an observer that retired them would change the
    command stream by dropping completions.
    """
    from util import runtime_gateway

    from saitenka.app.controller import Reader
    from saitenka.app.subtitle_render import NullRenderer

    def commands_for(*, observing: bool) -> list[tuple]:
        ipc = FakeIPC()
        gateway = runtime_gateway(ipc)  # the REAL gateway, whose router is the sole consumer
        reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
        try:
            if observing:
                gateway.observe(
                    SessionReactor(
                        _state(),
                        OwnerRouter(SessionReducer({}), lambda _e: None),
                        gateway.mailbox,
                        lambda _effect: True,
                    )
                )
            reader.set_subtitle("猫を見る")
            reader.pump()
            reader.pump()
            return list(ipc.commands)
        finally:
            reader.close()
            gateway.close()

    assert commands_for(observing=True) == commands_for(observing=False)


def test_every_fire_and_forget_effect_reaches_the_dispatcher() -> None:
    """`_apply`'s fire-and-forget branch must cover exactly `FireAndForget`.

    Not a style point: an effect in the alias but missing from the branch falls through to the
    async path, which reads `.effect_id` — an attribute a lifecycle effect does not carry. That is
    an `AttributeError` mid-close, after some participants have already run. This walks the alias
    so adding a member without extending the branch fails here rather than during a teardown.
    """
    import dataclasses
    from typing import get_args

    from saitenka.runtime.effects import FireAndForget

    dispatched: list[object] = []
    reactor = SessionReactor(
        _state(),
        OwnerRouter(SessionReducer({}), lambda _e: None),
        SessionMailbox(),
        lambda effect: bool(dispatched.append(effect)) or True,
    )

    members = get_args(FireAndForget.__value__)
    assert members  # negative control: an empty alias would make this vacuous
    for effect_type in members:
        # Placeholder values by field type, so a member with a payload needs no hand-written case.
        kwargs = {f.name: "" for f in dataclasses.fields(effect_type)}
        reactor._apply(effect_type(**kwargs))

    assert [type(effect) for effect in dispatched] == list(members)
