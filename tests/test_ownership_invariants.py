"""The ownership model's structural invariants, exhaustive over what the reactor actually registers.

Ownership is the axis the runtime dispatches on: `SessionState` has one slot per `Owner`,
`owner_of` routes, `RouteKey(event, Owner)` keys the table. Everything below is derived from the
live reactor rather than from a list, so a new payload / owner / feature is covered without anyone
remembering to add a row — a declared model that nothing checks drifts into decoration, and the
failure is silent: an unowned payload routes to nobody and is merely *counted*.

What is deliberately not asserted here: which owner a given payload belongs to. That is a judgement
the route table states, and restating it would be a second copy of the same fact.
"""

from __future__ import annotations

import dataclasses
import typing

import pytest
from util import FakeIPC, bare_gateway

import saitenka.app.session.routes as session_routes
from saitenka.runtime import Owner
from saitenka.runtime import events as event_types
from saitenka.runtime.state import SessionState

#: Payloads consumed below the reducer graph. Declared because "another tier owns it" and "nobody
#: owns it" are indistinguishable from the router's side.
_LOOP_TIER = ("CloseRequested", "CommandHandled", "RawMpvEvent")
_CONTROLLER_TIER = tuple(payload.__name__ for payload in session_routes._PASSTHROUGH)


@pytest.fixture
def reactor():
    gateway = bare_gateway(FakeIPC())
    session_routes.install_session_reactor(gateway)
    try:
        yield gateway.session_reactor
    finally:
        gateway.close()


def _payload_types() -> list[type]:
    """Every leaf of the `RuntimeEvent` union — the closed set of things a session can be told."""

    def leaves(annotation, seen=None):
        seen = seen if seen is not None else set()
        if annotation in seen:
            return []
        seen.add(annotation)
        value = getattr(annotation, "__value__", None)
        arguments = typing.get_args(value if value is not None else annotation)
        if not arguments:
            return [annotation]
        return [leaf for argument in arguments for leaf in leaves(argument, seen)]

    return sorted(
        {t for t in leaves(event_types.RuntimeEvent) if isinstance(t, type)},
        key=lambda t: t.__name__,
    )


def test_every_owner_has_exactly_one_slot_and_every_slot_an_owner():
    """The parity the whole model rests on: `owner_of` returns an `Owner`, and the router indexes
    `SessionState` by its value. A slot with no owner is unreachable; an owner with no slot raises
    mid-turn, in the router, on whatever event happened to arrive first."""
    owners = {owner.value for owner in Owner}
    slots = {field.name for field in dataclasses.fields(SessionState)}

    assert owners == slots


def test_every_event_payload_is_routed_broadcast_or_declared_as_a_lower_tier():
    """Exhaustive over the union. An unowned payload is not an error at runtime — `OwnerRouter`
    counts it and returns the state unchanged — so it can be published for months and do nothing.
    This is the assertion that makes that visible at the moment the payload is added."""
    declared = {*_LOOP_TIER, *_CONTROLLER_TIER}
    broadcast = {t.__name__ for t in session_routes.LIFETIME_EVENTS}

    unowned = [
        payload.__name__
        for payload in _payload_types()
        if payload is not event_types.EffectFinished  # carries its own owner field
        and payload.__name__ not in declared
        and payload.__name__ not in broadcast
        and session_routes.owner_of(object.__new__(payload)) is None
    ]

    assert not unowned, (
        f"payloads that route to nobody: {unowned}. Give each an owner in `owner_of`, or declare "
        "it as loop-tier / controller-tier here with the reason."
    )


def test_the_declared_lower_tiers_are_still_payloads():
    """The exemption list's own negative control. A renamed payload would leave a stale name here
    that excuses nothing, and the sweep above would keep passing while the real payload went
    unowned."""
    names = {payload.__name__ for payload in _payload_types()}

    assert set(_LOOP_TIER) <= names
    assert set(_CONTROLLER_TIER) <= names


def test_every_slot_holds_at_least_one_feature(reactor):
    """A slot is filled by producing its reducer, never by wiring an empty one. An empty slice
    accepts every event for that owner and decides nothing — which reads exactly like a working
    owner from the outside."""
    empty = [
        slot
        for slot in (field.name for field in dataclasses.fields(SessionState))
        if not getattr(reactor.state, slot).features
    ]

    assert not empty, f"owner slots with no feature registered: {empty}"


def test_every_claimed_payload_is_one_the_reactor_routes():
    """`_CLAIMED` withholds a payload from the SessionController. A claim ahead of its route routes fine,
    reduces nothing, and silently stops doing the thing the SessionController used to do — the failure the
    migration plan names, stated as a test rather than as a paragraph."""
    unroutable = [
        payload.__name__
        for payload in session_routes._CLAIMED
        if session_routes.owner_of(object.__new__(payload)) is None
        and payload not in session_routes.LIFETIME_EVENTS
    ]

    assert not unroutable, f"claimed from the SessionController but routed to nobody: {unroutable}"


def test_owner_thread_events_bypass_reducers_without_being_claimed():
    """The session loop orders these payloads; the bounded session shell performs them."""
    forwarded = {event_types.FileLoaded, event_types.UserCommand}

    assert forwarded == set(session_routes._PASSTHROUGH)
    assert forwarded.isdisjoint(session_routes._SESSION_EVENTS)
    assert forwarded.isdisjoint(session_routes._CLAIMED)


def test_the_payload_sweep_is_not_vacuous():
    """Guards the derivation: if the union ever stops resolving, every sweep above passes over an
    empty set and proves nothing."""
    payloads = _payload_types()

    assert len(payloads) > 20
    assert event_types.UserCommand in payloads
