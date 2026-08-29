"""The session's loop: receive one envelope, show it to the reactor, hand on what it did not take.

The receive lives here rather than on the consumer because *when to wake* is one question with two
halves that only this module holds together: an envelope arriving, and the earliest armed timer
coming due. A consumer that asked for a batch could only guess the second, which is what the
retired poll interval was doing.

What the session does with a payload is not this module's business — `receive` takes the handler,
and `run` takes the turn. That keeps `runtime` free of `app`, and it is also why the loop can drive
a session whose reactor claims everything and one that has no reactor at all.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import TYPE_CHECKING

from saitenka.runtime.events import (
    CloseRequested,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EffectFinished,
    EventEnvelope,
    EventOrigin,
    FileLoaded,
    PropertyObserved,
    RawMpvEvent,
    RuntimeEvent,
    UserCommand,
)
from saitenka.runtime.runner import SessionRunner

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.correlator import EffectCorrelator
    from saitenka.runtime.mailbox import SessionMailbox
    from saitenka.runtime.reactor import SessionReactor


class SessionLoop:
    """The mailbox's sole consumer, and the thing that drives turns off it."""

    def __init__(
        self,
        mailbox: SessionMailbox,
        correlator: EffectCorrelator,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mailbox = mailbox
        self._clock = clock
        self._correlator = correlator
        self._reactor: SessionReactor | None = None
        self._claims: Callable[[RuntimeEvent], bool] = lambda _payload: False
        #: Envelopes seen, and how many the reactor owned outright. The `session-loop` duty retires
        #: when the second reaches the first: the loop already receives from the mailbox and the
        #: reactor already sees every envelope, so what is left is the session still *acting* on
        #: what nothing claimed. Counted per payload type, because the tail is what says which
        #: feature is next and a bare ratio does not.
        self._seen: Counter[str] = Counter()
        self._claimed: Counter[str] = Counter()

    @property
    def mailbox(self) -> SessionMailbox:
        """The session's mailbox. Exposed for an observer to be constructed against — a consumer
        must still go through `observe`, since this loop is the only sanctioned one."""
        return self._mailbox

    def routing_census(self) -> dict[str, tuple[int, int]]:
        """`{payload type: (reactor-owned, seen)}` for this session."""
        return {name: (self._claimed[name], seen) for name, seen in self._seen.items()}

    def observe(
        self, reactor: SessionReactor, claims: Callable[[RuntimeEvent], bool] | None = None
    ) -> None:
        """Give the reactor every envelope this loop sees, terminals included.

        Fanned out HERE rather than from the session's turn because this is the mailbox's declared
        sole consumer, and `SessionReactor.handle` takes an envelope without reading the mailbox —
        so a second observer costs no envelope. `run_until_idle` would consume, and must not be used
        while this loop exists.

        Terminals are safe to fan out because the reactor retires only what it dispatched; an
        effect it never issued is not its to complete.

        A payload accepted by `claims` is the reactor's alone and is withheld from the owner-thread
        controller, so one duty runs once. The claim set is declared rather than inferred from the
        route table: routing says which reducer observes an event; claiming says whether any
        owner-thread work remains after that observation.
        """
        if self._reactor is not None:
            raise RuntimeError("reactor already observing")
        self._reactor = reactor
        if claims is not None:
            self._claims = claims

    def run(self, turn: Callable[[float | None], bool], *, until: Callable[[], bool]) -> None:
        """Drive turns until the session stops itself or `until` says so.

        No interval and no tick: a turn blocks on the transport until something happens, bounded by
        the earliest armed timer, so a deadline that produces no mpv event comes due on time rather
        than within a tick of it, and an idle session with nothing armed does not wake at all.

        `turn` reports whether the session can continue — the transport going away is not a stop
        anyone asked for, so it cannot be expressed as a predicate over session state.
        """
        alive = True

        def step(timeout: float | None) -> None:
            nonlocal alive
            alive = turn(timeout)

        SessionRunner(step, clock=self._clock).run_until(lambda: not alive or until())

    def receive(self, timeout: float | None, handle: Callable[[object], None]) -> None:
        """Take one batch, handing each payload on in mailbox sequence.

        Pushed rather than returned as a batch, which is what retires the ordered-terminal mode: a
        completion used to be either dispatched mid-drain (ahead of every observation the caller had
        not seen yet) or returned for the caller to dispatch in position. With the handler called as
        each envelope is popped, "in position" and "now" are the same instant.
        """
        self._correlator.publish_due()
        timeout = self._bounded_by_deadline(timeout)
        head = None
        if timeout is None or timeout > 0:
            head = self._mailbox.receive(timeout=timeout)
        # The envelope we blocked for is part of the batch, not a turn of its own: coalescing is
        # over adjacent envelopes, so handling the head separately would let a repeat arriving
        # behind it through — the one press in ten that woke the loop rather than joining a batch
        # already there.
        for envelope in self._mailbox.receive_ready(start=head):
            self._turn(envelope, handle)

    def _bounded_by_deadline(self, timeout: float | None) -> float | None:
        """Never block past the earliest armed timer.

        A timer that fires without producing an mpv event is otherwise invisible until something
        else arrives, which for an idle session is never — the job the retired poll interval was
        doing. `publish_due` has already run, so anything due now is in the mailbox and this only
        ever looks forward.
        """
        due_at = self._correlator.next_deadline
        if due_at is None:
            return timeout
        remaining = max(0.0, due_at - self._clock())
        return remaining if timeout is None else min(timeout, remaining)

    def _turn(self, envelope: EventEnvelope, handle: Callable[[object], None]) -> None:
        """One envelope: the reactor always sees it; the session sees it unless the reactor owns it.

        The order matters — the reactor's dispatch has always preceded the session's handling of the
        same envelope, and keeping it means a claim changes *who* acts, never *when*.
        """
        # Asked BEFORE the reactor runs: handling a completion retires it from `_pending`, so an
        # ownership question asked afterwards always answers "no" and every claimed terminal would
        # fall through to the correlator as well.
        claimed = self._claims(envelope.payload)
        name = type(envelope.payload).__name__
        self._seen[name] += 1
        if claimed:
            self._claimed[name] += 1
        self._observe(envelope)
        if not claimed:
            self._route(envelope.payload, handle)

    def announce(self, event: RuntimeEvent, now: float, connection_epoch: int) -> bool:
        """Show the reactor one event that never went through the mailbox.

        The sequence number is the mailbox's ordering device and the reactor does not read it, so
        an off-mailbox announcement carries 0 rather than inventing one that would collide.
        """
        if self._reactor is None:
            return False
        return self._reactor.handle(
            EventEnvelope(0, now, EventOrigin.LIFECYCLE, connection_epoch, event)
        )

    def _observe(self, envelope: EventEnvelope) -> None:
        if self._reactor is not None:
            self._reactor.handle(envelope)

    def _route(self, payload: RuntimeEvent, handle: Callable[[object], None]) -> None:
        if isinstance(payload, CloseRequested):
            reason = (
                "runtime mailbox overloaded"
                if payload.reason == "runtime-overloaded"
                else payload.reason
            )
            raise OSError(reason)
        if isinstance(payload, EffectFinished):
            # A completion belongs to the correlator that issued the effect, and never went out to
            # the session for any reason but order.
            self._correlator.handle_terminal(payload)
            return
        if isinstance(payload, RawMpvEvent) and isinstance(payload.data, dict):
            handle(payload.data)
        elif isinstance(
            payload,
            UserCommand
            | ConnectionLost
            | ConnectionReady
            | ConnectionReplaced
            | FileLoaded
            | PropertyObserved,
        ):
            handle(payload)
