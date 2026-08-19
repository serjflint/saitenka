"""Typed mpv adapter: wire messages in, correlated runtime outcomes out."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.runtime import (
    CloseRequested,
    CommandHandled,
    ConnectionLost,
    ConnectionReady,
    ConnectionReplaced,
    EffectError,
    EffectFinished,
    EffectOutcome,
    EventEnvelope,
    EventOrigin,
    ExpireEffect,
    MailboxFull,
    Owner,
    RawMpvEvent,
    RuntimeEvent,
    SendMpvCommand,
    SessionMailbox,
    SurfaceTransaction,
    TrafficClass,
    UserCommand,
)
from saitenka.runtime.jobs import JobBroker, JobLanePolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.ipc import IPCRequest, MpvIPC
    from saitenka.runtime.legacy import LegacyRuntimeBridge
    from saitenka.runtime.reactor import SessionReactor


@dataclass(frozen=True, slots=True)
class GatewaySnapshot:
    connection_epoch: int
    pending: int
    stale_outcomes: int
    inbound_overloads: int
    command_outcomes: int


@dataclass(frozen=True, slots=True)
class ReconnectRetry:
    connection_epoch: int
    attempt: int


class ConnectionPhase(StrEnum):
    READY = "ready"
    LOST = "lost"
    CONNECTING = "connecting"
    REPLAYING = "replaying"
    CLOSED = "closed"


_CANDIDATE_EVENT_LIMIT = 256


class LegacyEventRouter:
    """Temporary sole consumer that presents mailbox observations to Reader unchanged."""

    def __init__(self, mailbox: SessionMailbox) -> None:
        self._mailbox = mailbox
        self._runtime_bridge: LegacyRuntimeBridge | None = None
        self._reactor: SessionReactor | None = None
        self._claims: Callable[[RuntimeEvent], bool] = lambda _payload: False

    def install_runtime_bridge(self, bridge: LegacyRuntimeBridge) -> None:
        if self._runtime_bridge is not None:
            raise RuntimeError("runtime bridge already installed")
        self._runtime_bridge = bridge

    @property
    def mailbox(self) -> SessionMailbox:
        """The session's mailbox. Exposed for an observer to be constructed against — a consumer
        must still go through `observe`, since the router is the only sanctioned one."""
        return self._mailbox

    def observe(
        self, reactor: SessionReactor, claims: Callable[[RuntimeEvent], bool] | None = None
    ) -> None:
        """Give the reactor every envelope this consumer sees, terminals included.

        Fanned out HERE rather than from the Reader's turn because this is the mailbox's declared
        sole consumer, and `SessionReactor.handle` takes an envelope without reading the mailbox —
        so a second observer costs no envelope. `run_until_idle` would consume, and must not be used
        while this router exists.

        Terminals are safe to fan out because the reactor retires only what it dispatched; an
        effect it never issued is not its to complete.

        `claims` is the migration's fallthrough seam: a payload it accepts is the reactor's alone
        and is withheld from the legacy Reader, so a migrated duty runs once rather than twice.
        Everything else falls through untouched. **It is a declared set, never derived from the
        route table** — the two are not the same question. `ConnectionReplaced` is routed to the
        startup-hint reducer *and* still needed by `subtitle_pipeline.connection_replaced`;
        inferring "routed implies claimed" would silently stop reconnects reaching the pipeline.
        """
        if self._reactor is not None:
            raise RuntimeError("reactor already observing")
        self._reactor = reactor
        if claims is not None:
            self._claims = claims

    def drain_events(
        self, timeout: float | None = 0.0, *, ordered_terminals: bool = False
    ) -> list[object]:
        """Drain one batch. With `ordered_terminals`, completions are returned in envelope
        sequence for the caller to dispatch instead of being run inline.

        Inline dispatch runs a completion's callback *during* the drain, so it precedes every
        observation the caller has not handled yet — the reverse of mailbox sequence, and of the
        order the WP6 runner will deliver. Callers that own a whole turn ask for ordered
        terminals; `Reader._drive_annotation_once` must not, because it drains from inside cue
        construction and a due event dispatched there would reenter mid-build.
        """
        if self._runtime_bridge is not None:
            self._runtime_bridge.publish_due()
        events: list[object] = []
        if timeout is None or timeout > 0:
            envelope = self._mailbox.receive(timeout=timeout)
            if envelope is not None:
                self._turn(envelope, events, ordered_terminals=ordered_terminals)
        for envelope in self._mailbox.receive_ready():
            self._turn(envelope, events, ordered_terminals=ordered_terminals)
        return events

    def _turn(
        self, envelope: EventEnvelope, events: list[object], *, ordered_terminals: bool
    ) -> None:
        """One envelope: the reactor always sees it; the Reader sees it unless the reactor owns it.

        The order matters — the reactor's dispatch has always preceded the Reader's handling of the
        same envelope, and keeping it means a claim changes *who* acts, never *when*.
        """
        # Asked BEFORE the reactor runs: handling a completion retires it from `_pending`, so an
        # ownership question asked afterwards always answers "no" and every claimed terminal would
        # fall through to the bridge as well.
        claimed = self._claims(envelope.payload)
        self._observe(envelope)
        if not claimed:
            self._route(envelope.payload, events, ordered_terminals=ordered_terminals)

    def announce(self, event: RuntimeEvent, now: float, connection_epoch: int) -> bool:
        """Show the reactor one event that never went through the mailbox.

        The sequence number is the mailbox's ordering device and the reactor does not read it, so
        an off-mailbox announcement carries 0 rather than inventing one that would collide.
        """
        if self._reactor is None:
            return False
        self._reactor.handle(EventEnvelope(0, now, EventOrigin.LIFECYCLE, connection_epoch, event))
        return True

    def _observe(self, envelope: EventEnvelope) -> None:
        if self._reactor is not None:
            self._reactor.handle(envelope)

    def _route(
        self, payload: RuntimeEvent, events: list[object], *, ordered_terminals: bool = False
    ) -> None:
        if isinstance(payload, CloseRequested):
            reason = (
                "runtime mailbox overloaded"
                if payload.reason == "runtime-overloaded"
                else payload.reason
            )
            raise OSError(reason)
        if isinstance(payload, EffectFinished):
            if ordered_terminals:
                events.append(payload)
            elif self._runtime_bridge is not None:
                self._runtime_bridge.handle_terminal(payload)
            return
        if isinstance(payload, RawMpvEvent) and isinstance(payload.data, dict):
            events.append(payload.data)
        elif isinstance(
            payload, UserCommand | ConnectionLost | ConnectionReady | ConnectionReplaced
        ):
            events.append(payload)


class MpvGateway:
    def __init__(
        self,
        ipc: MpvIPC,
        mailbox: SessionMailbox,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ipc = ipc
        self._mailbox = mailbox
        self._clock = clock
        self._lock = threading.Lock()
        self._surface_lock = threading.Lock()
        self._surface_revisions: dict[str, int] = {}
        self._pending: dict[int, tuple[SendMpvCommand, IPCRequest]] = {}
        self._connection_epoch = 0
        self._connection_phase = ConnectionPhase.READY
        self._candidate_events: list[dict] = []
        self._next_command_id = 0
        self._stale_outcomes = 0
        self._inbound_overloads = 0
        self._command_outcomes = 0
        self._observers: tuple[str, ...] = ()
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_epoch: int | None = None
        self._reconnect_attempt = 0
        self._closed = False
        self._ready = False
        #: Session resources whose *lifetime* the runtime owns, by name. Deliberately opaque:
        #: `mpvio` must not import `app`, and only the app-side dispatcher knows what a name
        #: means. An owner hands its resource over at construction and stops closing it itself.
        self.session_resources: dict[str, object] = {}
        self._router = router = LegacyEventRouter(mailbox)
        ipc.install_runtime_ingress(
            self._publish_observation,
            self._publish_connection,
            router.drain_events,
            self,
        )
        from saitenka.runtime.legacy import LegacyRuntimeBridge

        self._jobs = JobBroker(mailbox)
        self._legacy = LegacyRuntimeBridge(
            mailbox,
            self,
            router,
            job_adapter=self._jobs,
            clock=clock,
        )
        with self._lock:
            self._ready = True
        self._start_pending_reconnect()

    @property
    def mailbox(self) -> SessionMailbox:
        """The session's mailbox. Exposed for an observer to be constructed against — a consumer
        must still go through `observe`, since the router is the only sanctioned one."""
        return self._mailbox

    def observe(
        self, reactor: SessionReactor, claims: Callable[[RuntimeEvent], bool] | None = None
    ) -> None:
        """Let a `SessionReactor` see the session, and own the part of it `claims` accepts.

        The gateway owns the mailbox's sole consumer, so this is the only place an observer can be
        attached without splitting the envelope stream. See `LegacyEventRouter.observe` for what
        claiming means and why it is declared rather than derived.
        """
        self._router.observe(reactor, claims)

    def publish_session_event(self, event: RuntimeEvent) -> bool:
        """Put a session-lifecycle fact on the mailbox for the reactor to route.

        Lifecycle traffic, not normal: these are session-shaped announcements ("we are ready"),
        and the normal lane is sized for the mpv event stream that can flood it.
        """
        with self._lock:
            if self._closed:
                return False
        try:
            self._mailbox.publish(
                event,
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=self.connection_epoch,
            )
        except MailboxFull:
            return False
        return True

    def deliver_session_event(self, event: RuntimeEvent) -> bool:
        """Hand a session fact straight to the observing reactor, bypassing the mailbox.

        For facts that arise *after* the session loop has stopped — close, above all. Publishing
        one would need someone to drain it, and a drain during teardown runs a full domain turn
        (cue settlement, reconcile) against half-closed collaborators. `handle` reads nothing but
        the envelope, so this is the whole cost.
        """
        return self._router.announce(event, self._clock(), self.connection_epoch)

    def dispatch_effect(self, effect) -> bool:
        """Perform one reactor-issued effect. Only the kinds an owner has actually migrated."""
        if isinstance(effect, SendMpvCommand):
            return self.dispatch(effect)
        return False

    @property
    def connection_epoch(self) -> int:
        with self._lock:
            return self._connection_epoch

    @property
    def legacy(self) -> LegacyRuntimeBridge:
        return self._legacy

    def submit_mpv(self, **kwargs) -> bool:
        """Submit one correlated command for a compatibility-owned runtime slice."""
        return self._legacy.submit_mpv(**kwargs)

    def schedule_timer(self, **kwargs) -> bool:
        return self._legacy.schedule_timer(**kwargs)

    def cancel_timer(self, timer: str) -> bool:
        return self._legacy.cancel_timer(timer)

    def dispatch_terminal(self, completion: EffectFinished) -> None:
        """Run a completion that `drain_events(ordered_terminals=True)` handed to the caller."""
        self._legacy.handle_terminal(completion)

    def register_job_lane(self, name: str, policy: JobLanePolicy, handler) -> None:
        self._jobs.register(name, policy, handler)

    def submit_job(self, **kwargs) -> bool:
        return self._legacy.submit_job(**kwargs)

    def close_job_lane(self, name: str, timeout: float = 2.0) -> bool:
        return self._jobs.close_lane(name, timeout)

    def register_observers(self, names: tuple[str, ...]) -> dict[str, dict]:
        """Own observer IDs and initial snapshots; reconnect replays the same closed set."""
        with self._lock:
            if self._closed:
                return {}
            self._observers = tuple(names)
        replies: dict[str, dict] = {}
        for observer_id, name in enumerate(names, 1):
            self._ipc.command("observe_property", observer_id, name)
            replies[name] = self._ipc.command("get_property", name)
        return replies

    def close(self) -> None:
        self._jobs.close()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection_phase = ConnectionPhase.CLOSED
            self._candidate_events.clear()
            thread = self._reconnect_thread
        self._legacy.cancel_timer("mpv-reconnect")
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    @property
    def snapshot(self) -> GatewaySnapshot:
        with self._lock:
            return GatewaySnapshot(
                self._connection_epoch,
                len(self._pending),
                self._stale_outcomes,
                self._inbound_overloads,
                self._command_outcomes,
            )

    def dispatch(self, effect: SendMpvCommand) -> bool:
        identity = effect.identity
        if isinstance(identity, SurfaceTransaction):
            # Serialize admission through command_async so an older revision can never be queued
            # behind a newer write for the same stable mpv overlay slot.
            with self._surface_lock:
                latest = self._surface_revisions.get(identity.slot, 0)
                if identity.revision <= latest:
                    self._publish_terminal(effect, EffectOutcome.SUPERSEDED)
                    return True
                self._surface_revisions[identity.slot] = identity.revision
                return self._dispatch_current(effect)
        return self._dispatch_current(effect)

    def _dispatch_current(self, effect: SendMpvCommand) -> bool:
        with self._lock:
            if (
                self._connection_phase is not ConnectionPhase.READY
                or effect.connection_epoch != self._connection_epoch
            ):
                return False
            if effect.effect_id.value in self._pending:
                return False
        request = self._ipc.command_async(
            *effect.command,
            expected_connection_epoch=effect.connection_epoch,
        )
        if not request.accepted:
            return False
        with self._lock:
            self._pending[effect.effect_id.value] = (effect, request)
        request.future.add_done_callback(lambda future: self._reply(effect.effect_id.value, future))
        return True

    def expire(self, control: ExpireEffect) -> None:
        with self._lock:
            pending = self._pending.pop(control.target_effect_id.value, None)
        if pending is None:
            self._record_stale()
            return
        effect, _request = pending
        self._publish_terminal(effect, EffectOutcome.FAILED, EffectError.TIMEOUT)

    def _reply(self, effect_value: int, future) -> None:
        with self._lock:
            pending = self._pending.pop(effect_value, None)
        if pending is None:
            self._record_stale()
            return
        effect, _request = pending
        reply = future.result()
        if self._clock() > effect.deadline:
            self._publish_terminal(effect, EffectOutcome.FAILED, EffectError.TIMEOUT)
            return
        error = reply.get("error")
        if error == "success":
            self._publish_terminal(effect, EffectOutcome.SUCCEEDED, result=reply.get("data"))
            return
        code = {
            "disconnected": EffectError.DISCONNECTED,
            "timeout": EffectError.TIMEOUT,
            "overloaded": EffectError.OVERLOADED,
        }.get(error, EffectError.INVALID_RESULT)
        self._publish_terminal(effect, EffectOutcome.FAILED, code)

    def _publish_observation(self, message: dict, connection_epoch: int) -> None:
        with self._lock:
            if self._closed or connection_epoch != self._connection_epoch:
                return
            if self._connection_phase is ConnectionPhase.REPLAYING:
                if len(self._candidate_events) >= _CANDIDATE_EVENT_LIMIT:
                    self._inbound_overloads += 1
                    overloaded = True
                else:
                    self._candidate_events.append(dict(message))
                    overloaded = False
                if not overloaded:
                    return
            elif self._connection_phase is not ConnectionPhase.READY:
                return
            else:
                overloaded = False
        if overloaded:
            self._request_close(connection_epoch)
            return
        self._publish_ready_observation(message, connection_epoch)

    def _publish_ready_observation(
        self,
        message: dict,
        connection_epoch: int,
        *,
        lock_held: bool = False,
    ) -> None:
        payload = self._observation_payload(message, lock_held=lock_held)
        try:
            self._publish_observation_payload(payload, connection_epoch)
        except MailboxFull:
            self._record_inbound_overload(lock_held=lock_held)
            self._mailbox.publish(
                CloseRequested("runtime-overloaded"),
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=connection_epoch,
            )

    def _observation_payload(self, message: dict, *, lock_held: bool) -> RuntimeEvent:
        name = str(message.get("event", "unknown"))
        if name == "property-change" and message.get("name") == "mouse-pos":
            name = "mouse-pos"
        if name == "client-message":
            args = message.get("args")
            if lock_held:
                command_id = self._next_command_id
                self._next_command_id += 1
            else:
                with self._lock:
                    command_id = self._next_command_id
                    self._next_command_id += 1
            if isinstance(args, list | tuple) and args and isinstance(args[0], str):
                return UserCommand(args[0], tuple(args[1:]), command_id)
            return UserCommand("", command_id=command_id)
        return RawMpvEvent(name, message)

    def _publish_observation_payload(self, payload: RuntimeEvent, connection_epoch: int) -> None:
        if isinstance(payload, UserCommand):
            self._mailbox.publish_command(
                payload,
                origin=EventOrigin.MPV,
                connection_epoch=connection_epoch,
            )
        else:
            self._mailbox.publish(
                payload,
                origin=EventOrigin.MPV,
                traffic=TrafficClass.NORMAL,
                connection_epoch=connection_epoch,
            )

    def _record_inbound_overload(self, *, lock_held: bool) -> None:
        if lock_held:
            self._inbound_overloads += 1
        else:
            with self._lock:
                self._inbound_overloads += 1

    def publish_legacy_outcome(self, outcome: CommandHandled) -> None:
        """Publish a synchronous compatibility result into the ordered runtime stream."""

        if outcome.command_id is None:
            self._mailbox.publish(
                outcome,
                origin=EventOrigin.USER,
                traffic=TrafficClass.NORMAL,
                connection_epoch=self.connection_epoch,
            )
        else:
            published = self._mailbox.publish_command_terminal(
                outcome,
                origin=EventOrigin.USER,
                connection_epoch=self.connection_epoch,
            )
            with self._lock:
                if published:
                    self._command_outcomes += 1
                else:
                    self._stale_outcomes += 1

    def _publish_connection(self, state: str, connection_epoch: int) -> None:
        lost = state == "lost"
        with self._lock:
            if self._closed:
                return
            if state == "replaced":
                self._connection_epoch = connection_epoch
                self._reconnect_epoch = connection_epoch
                self._connection_phase = ConnectionPhase.REPLAYING
                self._candidate_events.clear()
                return
            was_ready = self._connection_phase is ConnectionPhase.READY
            self._connection_phase = ConnectionPhase.LOST
            self._candidate_events.clear()
        if was_ready:
            self._mailbox.publish(
                ConnectionLost(connection_epoch),
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=connection_epoch,
            )
        if lost:
            self._request_reconnect(connection_epoch)

    def _commit_replacement(self, connection_epoch: int, replay: tuple[dict, ...]) -> bool:
        with self._lock:
            if (
                self._closed
                or self._connection_phase is not ConnectionPhase.REPLAYING
                or connection_epoch != self._connection_epoch
                or self._ipc.disconnected
            ):
                return False
            candidate = tuple(self._candidate_events)
            self._candidate_events.clear()
            self._mailbox.publish(
                ConnectionReplaced(connection_epoch),
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=connection_epoch,
            )
            for message in (*replay, {"event": "file-loaded"}, *candidate):
                self._publish_ready_observation(message, connection_epoch, lock_held=True)
            self._mailbox.publish(
                ConnectionReady(connection_epoch),
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=connection_epoch,
            )
            self._connection_phase = ConnectionPhase.READY
            return True

    def _start_pending_reconnect(self) -> None:
        with self._lock:
            epoch = self._reconnect_epoch
        if epoch is not None:
            self._request_reconnect(epoch)

    def _request_reconnect(self, connection_epoch: int) -> None:
        with self._lock:
            if self._closed or connection_epoch != self._connection_epoch:
                return
            if not self._ready:
                self._reconnect_epoch = connection_epoch
                return
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                self._reconnect_epoch = connection_epoch
                return
            self._reconnect_epoch = connection_epoch
            self._connection_phase = ConnectionPhase.CONNECTING
            thread = threading.Thread(
                target=self._reconnect,
                name="mpv-reconnect",
                daemon=True,
            )
            self._reconnect_thread = thread
        thread.start()

    def _reconnect(self) -> None:
        if self._ipc.reconnect_once() and self._finish_reconnect():
            return
        self._handle_reconnect_failure()

    def _finish_reconnect(self) -> bool:
        epoch = self.connection_epoch
        replay = self._replay_observers(epoch)
        if replay is None or not self._commit_replacement(epoch, replay):
            return False
        with self._lock:
            self._reconnect_attempt = 0
            pending_epoch = self._reconnect_epoch
            self._reconnect_thread = None
            restart = self._connection_phase is ConnectionPhase.LOST
            if not restart and pending_epoch == epoch:
                self._reconnect_epoch = None
        if restart and pending_epoch is not None:
            self._request_reconnect(pending_epoch)
        return True

    def _handle_reconnect_failure(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection_phase = ConnectionPhase.LOST
            self._candidate_events.clear()
            self._reconnect_thread = None
            retry_epoch = self._connection_epoch
            self._reconnect_attempt += 1
            attempt = self._reconnect_attempt
        if self._ipc.reconnects_left <= 0:
            self._request_close(retry_epoch)
            return
        self._schedule_reconnect_retry(retry_epoch, attempt)

    def _schedule_reconnect_retry(self, retry_epoch: int, attempt: int) -> None:
        def retry(completion: EffectFinished) -> None:
            if completion.outcome is EffectOutcome.SUCCEEDED:
                self._request_reconnect(retry_epoch)
            elif completion.outcome is EffectOutcome.REJECTED:
                self._request_close(retry_epoch)

        if not self._legacy.schedule_timer(
            owner=Owner.SESSION,
            identity=ReconnectRetry(retry_epoch, attempt),
            timer="mpv-reconnect",
            due_at=self._clock() + min(0.25, 0.05 * attempt),
            on_finished=retry,
        ):
            self._request_close(retry_epoch)

    def _replay_observers(self, connection_epoch: int) -> tuple[dict, ...] | None:
        with self._lock:
            names = self._observers
            if (
                self._closed
                or self._connection_phase is not ConnectionPhase.REPLAYING
                or connection_epoch != self._connection_epoch
            ):
                return None
        replay: list[dict] = []
        for observer_id, name in enumerate(names, 1):
            if self._ipc.command("observe_property", observer_id, name).get("error") != "success":
                return None
            reply = self._ipc.command("get_property", name)
            if reply.get("error") != "success":
                return None
            replay.append({"event": "property-change", "name": name, "data": reply.get("data")})
        return tuple(replay)

    def _request_close(self, connection_epoch: int) -> None:
        self._mailbox.publish(
            CloseRequested("mpv-disconnected"),
            origin=EventOrigin.LIFECYCLE,
            traffic=TrafficClass.LIFECYCLE,
            connection_epoch=connection_epoch,
        )

    def _publish_terminal(
        self,
        effect: SendMpvCommand,
        outcome: EffectOutcome,
        error: EffectError | None = None,
        *,
        result: object = None,
    ) -> None:
        completion = EffectFinished(
            effect.effect_id,
            effect.owner,
            effect.identity,
            outcome,
            result,
            error,
        )
        if not self._mailbox.publish_terminal(
            completion,
            origin=EventOrigin.MPV,
            connection_epoch=effect.connection_epoch,
        ):
            self._record_stale()

    def _record_stale(self) -> None:
        with self._lock:
            self._stale_outcomes += 1


def install_legacy_gateway(ipc: MpvIPC) -> MpvGateway:
    return MpvGateway(ipc, SessionMailbox())


def register_observer_set(ipc, names: tuple[str, ...]) -> dict[str, dict]:
    """Register through the gateway when installed, or through a minimal test/pre-run adapter."""
    register = getattr(ipc, "register_runtime_observers", None)
    if register is not None:
        return register(names)
    replies: dict[str, dict] = {}
    for observer_id, name in enumerate(names, 1):
        ipc.command("observe_property", observer_id, name)
        replies[name] = ipc.command("get_property", name)
    return replies
