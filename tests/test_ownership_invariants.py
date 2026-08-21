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
from util import FakeIPC, runtime_gateway

from saitenka.app import session_routes
from saitenka.runtime import Owner
from saitenka.runtime import events as event_types
from saitenka.runtime.state import SessionState

#: Payloads the *loop* consumes rather than an owner slice: a stop signal and a command-id
#: retirement are session-lifecycle facts, not state any feature reduces. Declared, because
#: "nobody owns it" and "the tier below owns it" are indistinguishable from the router's side.
_LOOP_TIER = ("CloseRequested", "CommandHandled")

#: The wire shape, carried opaquely for a session with no gateway to name it. Not a payload any
#: owner can hold: it is the fallback path's input.
_UNGATEWAYED = ("RawMpvEvent",)


@pytest.fixture
def reactor():
    gateway = runtime_gateway(FakeIPC())
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
    declared = {*_LOOP_TIER, *_UNGATEWAYED}
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
        "it as loop-tier / un-gatewayed here with the reason."
    )


def test_the_declared_lower_tiers_are_still_payloads():
    """The exemption list's own negative control. A renamed payload would leave a stale name here
    that excuses nothing, and the sweep above would keep passing while the real payload went
    unowned."""
    names = {payload.__name__ for payload in _payload_types()}

    assert set(_LOOP_TIER) <= names
    assert set(_UNGATEWAYED) <= names


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
    """`_CLAIMED` withholds a payload from the Reader. A claim ahead of its route routes fine,
    reduces nothing, and silently stops doing the thing the Reader used to do — the failure the
    migration plan names, stated as a test rather than as a paragraph."""
    unroutable = [
        payload.__name__
        for payload in session_routes._CLAIMED
        if session_routes.owner_of(object.__new__(payload)) is None
        and payload not in session_routes.LIFETIME_EVENTS
    ]

    assert not unroutable, f"claimed from the Reader but routed to nobody: {unroutable}"


def test_no_arm_of_the_readers_fallback_drain_is_still_live_work():
    """Uplifted from the ledger's `loop_residue`, which lives in a git-ignored scratch file and so
    has never run in CI.

    `Reader._drain_event` is the no-reactor fallback. An arm whose payload the reactor *claims* is
    dead in a session that has one; an arm whose payload is unclaimed is a duty the Reader still
    performs, and the session-loop migration is not finished while one exists.

    **Arms are a subset of claims, never equal to them** — `_CLAIMED` also carries payloads that are
    published rather than received (`StartupHintRequested`, `StartupReady`, `SessionClosing`), which
    have no arm and never will. Written as equality this fails at HEAD and gets "fixed" with a
    hard-coded exception list, which is how a real invariant becomes a rubber stamp.
    """
    import ast
    from pathlib import Path

    controller = Path(session_routes.__file__).with_name("controller.py")
    drain = next(
        node
        for node in ast.walk(ast.parse(controller.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "_drain_event"
    )
    narrowed = {
        target.id if isinstance(target, ast.Name) else target.attr
        for node in ast.walk(drain)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) == 2
        and isinstance(target := node.args[1], ast.Name | ast.Attribute)
    }
    # Filtered against the event vocabulary rather than a stop-list: the drain also narrows `dict`
    # and `str` on its way into the raw arm, and those are guards, not payloads.
    arms = {name for name in narrowed if hasattr(event_types, name)}
    claimed = {payload.__name__ for payload in session_routes._CLAIMED} | {"EffectFinished"}

    assert arms, "no typed arm found in `_drain_event` — the sweep would be vacuous"
    assert arms <= claimed, (
        f"arms of the Reader's fallback drain that the reactor does not claim: {sorted(arms - claimed)}. "
        "Each is a duty the Reader still performs for a session that has a runtime."
    )


def test_the_payload_sweep_is_not_vacuous():
    """Guards the derivation: if the union ever stops resolving, every sweep above passes over an
    empty set and proves nothing."""
    payloads = _payload_types()

    assert len(payloads) > 20
    assert event_types.UserCommand in payloads
