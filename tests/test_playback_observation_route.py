"""One mpv property observation, three layers deep: who reduces it and who applies what it published.

`Owner.PLAYBACK`'s slice was already the one copy of the projection — what moved here is the
*decision* that an observation is a property change, from the SessionController's drain to the route table.
The deltas still have to reach the SessionController, so they arrive as `ApplyPlaybackDeltas` instead of as a
return value; a mailbox-delivered observation has no caller to hand them back to.

The hazard this pins is double reduction: the observation owner already routes through the
reactor, so an observation that was routed *and* left to fall through would reduce twice — the
projection would hide it (it is idempotent) and the deltas would not.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.session.controller import SessionController
from saitenka.app.session.routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer


def _session(*, reactor: bool):
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    if reactor:
        install_session_reactor(gateway, startup_hint=False)
    reader = SessionController(ipc, prefetch=False, renderer=NullRenderer())
    reader.playback_observation.install_seed({})
    return ipc, gateway, reader


@pytest.mark.parametrize("reactor", [True, False])
@pytest.mark.timeout(5)
def test_an_observation_lands_the_same_whether_the_reactor_owns_it(reactor) -> None:
    """The differential. With a reactor the payload is claimed and its deltas ride an effect; with
    only a gateway the same typed payload falls through to the SessionController's arm. The cue on screen is
    the observable both paths owe, and it must not depend on which one ran."""
    ipc, gateway, reader = _session(reactor=reactor)
    try:
        ipc.emit({"event": "property-change", "name": "sub-text", "data": "いち"})
        reader._drain_events()
        text, observed = reader.sub_text, reader.playback_observation.state.value("sub-text")
    finally:
        reader.close()
        gateway.close()

    assert (text, observed) == ("いち", "いち")


@pytest.mark.timeout(5)
def test_a_claimed_observation_is_reduced_by_exactly_one_owner() -> None:
    """Claimed as often as it is seen — the equality is what forbids the fall-through, and the
    fall-through is what would apply the turn's deltas twice."""
    ipc, gateway, reader = _session(reactor=True)
    try:
        for text in ("いち", "に", "さん"):
            ipc.emit({"event": "property-change", "name": "sub-text", "data": text})
        reader._drain_events()
        census = gateway.claim_census()["PropertyObserved"]
    finally:
        reader.close()
        gateway.close()

    assert census == (3, 3)


@pytest.mark.timeout(5)
def test_the_pointer_is_the_only_observation_that_coalesces_in_a_batch() -> None:
    """mpv reports the cursor faster than a turn can consume it, so the mailbox keeps the newest.
    Every other observation is a fact a later one may depend on having been seen, which is why the
    allowlist is one name and not a type."""
    ipc, gateway, reader = _session(reactor=True)
    try:
        for x in (1, 2, 3):
            ipc.emit({"event": "property-change", "name": "mouse-pos", "data": {"x": x, "y": 0}})
        ipc.emit({"event": "property-change", "name": "pause", "data": True})
        reader._drain_events()
        seen = gateway.claim_census()["PropertyObserved"]
        pointer = reader.playback_observation.state.value("mouse-pos")
    finally:
        reader.close()
        gateway.close()

    assert seen == (2, 2), "three pointer moves collapsed into the newest, the pause stayed its own"
    assert pointer == {"x": 3, "y": 0}
