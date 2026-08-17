"""Typed mpv adapter: wire messages in, correlated runtime outcomes out."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime import (
    CloseRequested,
    ConnectionLost,
    ConnectionReplaced,
    EffectError,
    EffectFinished,
    EffectOutcome,
    EventOrigin,
    ExpireEffect,
    MailboxFull,
    RawMpvEvent,
    SendMpvCommand,
    SessionMailbox,
    TrafficClass,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.mpvio.ipc import IPCRequest, MpvIPC


@dataclass(frozen=True, slots=True)
class GatewaySnapshot:
    connection_epoch: int
    pending: int
    stale_outcomes: int
    inbound_overloads: int


class LegacyEventRouter:
    """Temporary sole consumer that presents mailbox observations to Reader unchanged."""

    def __init__(self, mailbox: SessionMailbox) -> None:
        self._mailbox = mailbox

    def drain_events(self) -> list[dict]:
        events: list[dict] = []
        for envelope in self._mailbox.receive_ready():
            payload = envelope.payload
            if isinstance(payload, CloseRequested):
                raise OSError("runtime mailbox overloaded")
            if isinstance(payload, RawMpvEvent) and isinstance(payload.data, dict):
                events.append(payload.data)
        return events


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
        self._pending: dict[int, tuple[SendMpvCommand, IPCRequest]] = {}
        self._connection_epoch = 0
        self._stale_outcomes = 0
        self._inbound_overloads = 0
        router = LegacyEventRouter(mailbox)
        ipc.install_runtime_ingress(
            self._publish_observation,
            self._publish_connection,
            router.drain_events,
            self,
        )

    @property
    def snapshot(self) -> GatewaySnapshot:
        with self._lock:
            return GatewaySnapshot(
                self._connection_epoch,
                len(self._pending),
                self._stale_outcomes,
                self._inbound_overloads,
            )

    def dispatch(self, effect: SendMpvCommand) -> bool:
        with self._lock:
            if effect.connection_epoch != self._connection_epoch:
                return False
            if effect.effect_id.value in self._pending:
                return False
            request = self._ipc.command_async(*effect.command)
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
        name = str(message.get("event", "unknown"))
        if name == "property-change" and message.get("name") == "mouse-pos":
            name = "mouse-pos"
        try:
            self._mailbox.publish(
                RawMpvEvent(name, message),
                origin=EventOrigin.MPV,
                traffic=TrafficClass.NORMAL,
                connection_epoch=connection_epoch,
            )
        except MailboxFull:
            with self._lock:
                self._inbound_overloads += 1
            self._mailbox.publish(
                CloseRequested("runtime-overloaded"),
                origin=EventOrigin.LIFECYCLE,
                traffic=TrafficClass.LIFECYCLE,
                connection_epoch=connection_epoch,
            )

    def _publish_connection(self, state: str, connection_epoch: int) -> None:
        payload: ConnectionReplaced | ConnectionLost
        with self._lock:
            if state == "replaced":
                self._connection_epoch = connection_epoch
                payload = ConnectionReplaced(connection_epoch)
            else:
                payload = ConnectionLost(connection_epoch)
        self._mailbox.publish(
            payload,
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
