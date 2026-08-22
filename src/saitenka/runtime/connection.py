"""Whether the session has *observed* its transport go away, and come back.

Deliberately not the transport's own `ConnectionPhase`, which is the other answer to a
similar-sounding question. The gateway's phase flips on the IO thread the instant the socket dies;
this flips when the session reaches `ConnectionLost` in envelope order, behind every observation
mpv published before it died. Asking the transport would answer "gone" for a cue that arrived while
it was alive and is still queued — so this is not the mirror of a fact somebody else owns, it is a
second fact with its own ordering, and the sequence is the whole of it.

The epoch rides on both events and is deliberately not kept: correlating an effect against the
connection it was issued for is the gateway's job and it holds the in-flight requests to do it
with. What the session decides from is one bit — may work go out at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.effects import ReplaySubtitleSelection, RetireCueIdentity
from saitenka.runtime.events import ConnectionLost, ConnectionReady, ConnectionReplaced
from saitenka.runtime.state import OwnerSlice, ReduceResult

if TYPE_CHECKING:
    from saitenka.runtime.events import RuntimeEvent

#: The feature's key inside `Owner.SESSION`'s slice. Spelled here so the store and the registration
#: cannot drift.
CONNECTION_FEATURE = "connection"


@dataclass(frozen=True, slots=True)
class ConnectionState:
    """`ready` starts true: a session is composed around a connection that is already up, and a
    session that opened one and has heard nothing since has no reason to refuse work."""

    ready: bool = True


def reduce_connection(state: object, event: RuntimeEvent, /) -> ReduceResult:
    """`FeatureReducer` for `Owner.SESSION`'s view of its transport.

    The state is one bit; the acts ride out as effects, because both of them belong to owners this
    reducer cannot name. `runtime` must not know what a tooltip or a subtitle track is — it only
    has to decide *that* a stranded cue is stranded and *that* a replaced connection has never
    heard the track selection. The performers are registered app-side.

    No outbox: an outbox exists to hand a delta back for the caller to apply in its own frame, and
    neither of these is the caller's to apply. `ConnectionReady` is the one that decides nothing at
    all, which is exactly why it is the one payload here the reactor can claim outright.
    """
    assert isinstance(state, ConnectionState)
    if isinstance(event, ConnectionLost):
        return ReduceResult(ConnectionState(ready=False), effects=(RetireCueIdentity(),))
    if isinstance(event, ConnectionReady):
        return ReduceResult(ConnectionState(ready=True))
    if isinstance(event, ConnectionReplaced):
        return ReduceResult(state, effects=(ReplaySubtitleSelection(),))
    return ReduceResult(state)


def connection_slice_of(slot: object) -> ConnectionState:
    assert isinstance(slot, OwnerSlice)
    state = slot.get(CONNECTION_FEATURE)
    assert isinstance(state, ConnectionState)
    return state


class SessionRoutePort(Protocol):
    def route_session_lifecycle(self, envelope: object | None) -> object | None: ...


class ConnectionStore:
    """Where the session's view of its transport is kept — the reactor's slot, or here.

    Read-only against a reactor, unlike the interaction stores: `ConnectionLost` arrives *through
    the mailbox*, so the reactor has already reduced it by the time anything asks. `observed` is
    the un-routed half — a session with a gateway but no reactor still has the events handed to it
    and nothing else to reduce them.
    """

    def __init__(self, port: SessionRoutePort) -> None:
        self._state = ConnectionState()
        self._port: SessionRoutePort | None = (
            port if port.route_session_lifecycle(None) is not None else None
        )

    @property
    def current(self) -> ConnectionState:
        if self._port is None:
            return self._state
        return connection_slice_of(self._port.route_session_lifecycle(None))

    def observed(self, event: RuntimeEvent) -> None:
        """Reduce one connection fact, unless a reactor already did."""
        if self._port is None:
            result = reduce_connection(self._state, event)
            assert isinstance(result.state, ConnectionState)
            self._state = result.state
