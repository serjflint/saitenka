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
from session_builder import build_session
from util import FakeIPC, bare_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer


def _session():
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.graph.playback.install_seed({})
    return ipc, gateway, reader


@pytest.mark.timeout(5)
def test_an_observation_reaches_the_playback_owner_and_its_application() -> None:
    """The playback slice decides the observation; its typed delta updates the visible cue."""
    ipc, gateway, reader = _session()
    try:
        ipc.emit({"event": "property-change", "name": "sub-text", "data": "いち"})
        reader.pump()
        text, observed = (
            reader.graph.playback.cue.text,
            reader.graph.playback.state.value("sub-text"),
        )
    finally:
        reader.close()
        gateway.close()

    assert (text, observed) == ("いち", "いち")


@pytest.mark.timeout(5)
def test_a_claimed_observation_is_reduced_by_exactly_one_owner() -> None:
    """Claimed as often as it is seen — the equality is what forbids the fall-through, and the
    fall-through is what would apply the turn's deltas twice."""
    ipc, gateway, reader = _session()
    try:
        for text in ("いち", "に", "さん"):
            ipc.emit({"event": "property-change", "name": "sub-text", "data": text})
        reader.pump()
        census = gateway.routing_census()["PropertyObserved"]
    finally:
        reader.close()
        gateway.close()

    assert census == (3, 3)


@pytest.mark.timeout(5)
def test_the_pointer_is_the_only_observation_that_coalesces_in_a_batch() -> None:
    """mpv reports the cursor faster than a turn can consume it, so the mailbox keeps the newest.
    Every other observation is a fact a later one may depend on having been seen, which is why the
    allowlist is one name and not a type."""
    ipc, gateway, reader = _session()
    try:
        for x in (1, 2, 3):
            ipc.emit({"event": "property-change", "name": "mouse-pos", "data": {"x": x, "y": 0}})
        ipc.emit({"event": "property-change", "name": "pause", "data": True})
        reader.pump()
        seen = gateway.routing_census()["PropertyObserved"]
        pointer = reader.graph.playback.state.value("mouse-pos")
    finally:
        reader.close()
        gateway.close()

    assert seen == (2, 2), "three pointer moves collapsed into the newest, the pause stayed its own"
    assert pointer == {"x": 3, "y": 0}
