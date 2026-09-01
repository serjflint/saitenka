"""The loop seam between reduced state and owner-thread session work.

`SessionReactor` owns typed state and effects; `EffectCorrelator` owns callback-shaped correlated
effects: mpv commands, timers, and jobs. The seam has two halves, and the tests here pin both:

* **observe** — the reactor sees every envelope, so it can track epochs and its own completions.
* **claim** — an exclusively reduced payload is withheld from `SessionController`, so one duty runs
  once instead of twice. Claiming is declared rather than derived from routing: observation and
  exclusive ownership answer different questions.

Every hazard asserted here was found by executing the code, not by reading it.
"""

from __future__ import annotations

from util import FakeIPC

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.runtime import (
    EffectFinished,
    EffectOutcome,
    EventOrigin,
    Owner,
    RawMpvEvent,
    SessionMailbox,
    TrafficClass,
)
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


class _NoCommands:
    """A correlator needs somewhere to send an mpv command; these tests never issue one."""

    connection_epoch = 0

    def dispatch(self, _effect) -> bool:
        return False

    def expire(self, _control) -> None:
        return None


def _consumer(mailbox, *, clock=lambda: 0.0):
    """The mailbox's sole consumer, with the correlator it drives timers and completions through."""
    from saitenka.runtime.correlator import EffectCorrelator
    from saitenka.runtime.loop import SessionLoop

    correlator = EffectCorrelator(mailbox, _NoCommands(), clock=clock)
    return SessionLoop(mailbox, correlator, clock=clock), correlator


def _drain(consumer, timeout: float | None = 0.0) -> list:
    events: list = []
    consumer.receive(timeout, events.append)
    return events


def test_an_unrouted_event_is_inert_and_counted_not_raised() -> None:
    """`RouteError` subclasses `ValueError`, and `SessionController.pump` reads `ValueError` as "mpv went away".

    So letting one escape would end the session silently the first time an extension or diagnostic
    event arrived.
    """
    router = OwnerRouter(SessionReducer({}), lambda _event: Owner.PLAYBACK)
    state = _state()

    result_state, effects = router(
        state, RawMpvEvent("property-change", {"event": "property-change"})
    )

    assert result_state is state
    assert effects == ()
    assert router.unrouted == {"playback:RawMpvEvent": 1}


def test_a_passthrough_event_is_neither_reduced_nor_counted_unrouted() -> None:
    reduced: list[object] = []
    router = OwnerRouter(
        SessionReducer(
            {
                RouteKey(RawMpvEvent, Owner.PLAYBACK): lambda state, event: (
                    reduced.append(event),
                    ReduceResult(state),
                )[1]
            }
        ),
        lambda _event: Owner.PLAYBACK,
        passthrough=(RawMpvEvent,),
    )
    state = _state()

    result_state, effects = router(
        state, RawMpvEvent("property-change", {"event": "property-change"})
    )

    assert result_state is state
    assert effects == ()
    assert reduced == []
    assert router.unrouted == {}


def test_a_claimed_event_is_withheld_from_the_reader() -> None:
    """The fallthrough seam: a migrated duty runs in the reactor *instead of* the SessionController.

    Without the claim both act on the same envelope and the duty runs twice.
    """
    mailbox = SessionMailbox()
    consumer, _correlator = _consumer(mailbox)
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

    assert _drain(consumer) == [{"event": "unclaimed"}]


def test_every_claimed_payload_has_a_performer_for_the_act_it_takes_over(make_session) -> None:
    """Claiming withholds a payload from the SessionController, so an act the SessionController was performing has to
    have somewhere else to land — an effect with a registered performer.

    This is the failure the seam cannot report on its own. A claim added ahead of its performer
    routes fine, reduces fine, and silently stops doing the thing: `ConnectionReplaced` would be
    claimed away and reconnects would simply stop reaching the subtitle pipeline, with nothing
    raising. So the oracle is composition — every act named by a claimed payload's reducer resolves
    to a name a real session registered.
    """
    from util import bare_gateway

    from saitenka.app.session.routes import (
        _CLAIMED,
        _PARTICIPANT_OF,
        _RESOURCE_OF,
        _SESSION_EVENTS,
        install_session_reactor,
        owner_of,
    )
    from saitenka.app.subtitle_render import NullRenderer
    from saitenka.runtime.connection import ConnectionState, reduce_connection
    from saitenka.runtime.events import PLAYBACK_EVENTS, ConnectionLost, ConnectionReplaced

    # Not `_SESSION_EVENTS` alone any more: `PropertyObserved` is claimed and routed to
    # `Owner.PLAYBACK`, so the invariant is against the whole route table's vocabulary.
    routed = set(_SESSION_EVENTS) | set(PLAYBACK_EVENTS)
    assert set(_CLAIMED) <= routed, "a claim without a route reduces nothing"
    assert owner_of(ConnectionReplaced(1)) is Owner.SESSION

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    try:
        reader.start()
        registered = set(gateway.session_resources)
    finally:
        reader.close()
        gateway.close()

    for event in (ConnectionLost(1), ConnectionReplaced(1)):
        effects = reduce_connection(ConnectionState(), event).effects
        assert effects, f"{type(event).__name__} is claimed, so its act must ride out as an effect"
        for effect in effects:
            names = _RESOURCE_OF.get(type(effect)) or (_PARTICIPANT_OF[type(effect)],)
            assert set(names) <= registered


def test_a_correlator_owned_completion_is_never_claimed_by_the_reactor() -> None:
    """Claiming a completion is an ownership question, not a type question.

    The correlator and the reactor both issue effects. Claiming by type would strand every
    correlated command the correlator owns — its terminal would be withheld and its callback never run.
    """
    from util import bare_gateway

    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    reactor = install_session_reactor(gateway)
    try:
        correlated = gateway.mailbox.allocate_effect()
        assert reactor.owns(correlated) is False
    finally:
        gateway.close()


def test_effect_ids_from_one_mailbox_never_collide_across_allocators() -> None:
    """D2: the mailbox owns the ID because the mailbox is what the ID must be unique within.

    Before this, the correlator counted from 0 privately. A second allocator — which is
    exactly what D3 makes the reactor — would have reissued IDs the correlator still held, and the
    only symptom is `reserve_terminal` returning False, which every caller already treats as an
    overloaded lane.
    """
    mailbox = SessionMailbox()
    correlator_ids = [mailbox.allocate_effect() for _ in range(3)]
    reactor_ids = mailbox.allocate_effects(3)

    assert set(correlator_ids).isdisjoint(reactor_ids)
    # Every one is reservable: a collision would surface here as a False, not as an exception.
    assert all(mailbox.reserve_terminal(effect_id) for effect_id in (*correlator_ids, *reactor_ids))


def test_a_completion_reaches_the_correlator_that_issued_it_past_the_observer() -> None:
    """The hazard that silently breaks every correlated effect.

    Measured before `_finish` checked ownership: the reactor retired a completion it never
    dispatched, the correlator's own `retire_terminal` then returned False, and it dropped the
    completion — so whatever awaited that effect waited forever. The oracle is that consequence and
    not the bookkeeping: the callback runs, which it cannot if anything upstream retired the
    reservation first.

    Outlives D4 in meaning, not in form: the *rule* (retiring is a claim of ownership) is
    permanent; only the second owner here is temporary.
    """
    mailbox = SessionMailbox()
    consumer, correlator = _consumer(mailbox)
    consumer.observe(_reactor(mailbox, OwnerRouter(SessionReducer({}), lambda _e: None)))
    finished: list[EffectFinished] = []
    assert correlator.schedule_timer(
        owner=Owner.PLAYBACK,
        identity="identity",
        timer="probe",
        due_at=0.0,
        on_finished=finished.append,
    )

    _drain(consumer)

    assert [(item.result, item.outcome) for item in finished] == [
        ("probe", EffectOutcome.SUCCEEDED)
    ]


def test_the_observer_sees_domain_events_without_consuming_them() -> None:
    """An observer sees an envelope without consuming the session's delivery of it."""
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

    consumer, _correlator = _consumer(mailbox)
    consumer.observe(_reactor(mailbox, router))

    mailbox.publish(
        RawMpvEvent("property-change", {"event": "property-change", "name": "pause"}),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )
    events = _drain(consumer)

    assert len(seen) == 1  # the reactor saw it
    assert events == [
        {"event": "property-change", "name": "pause"}
    ]  # and so did the SessionController


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

    def flatten(alias) -> tuple[type, ...]:
        # A member may itself be an alias (`StartupEffect`), and `get_args` stops at the first
        # level — walking it is what keeps the pin honest as the vocabulary grows by group.
        out: list[type] = []
        for member in get_args(alias):
            out.extend(flatten(member.__value__) if hasattr(member, "__value__") else [member])
        return tuple(out)

    members = flatten(FireAndForget.__value__)
    assert members  # negative control: an empty alias would make this vacuous
    for effect_type in members:
        # Placeholder values by field type, so a member with a payload needs no hand-written case.
        kwargs = {f.name: "" for f in dataclasses.fields(effect_type)}
        reactor._apply(effect_type(**kwargs))

    assert [type(effect) for effect in dispatched] == list(members)
