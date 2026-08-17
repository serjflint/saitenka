from __future__ import annotations

import threading
from concurrent.futures import Future

from saitenka.mpvio.gateway import MpvGateway
from saitenka.mpvio.ipc import IPCRequest, MpvIPC
from saitenka.runtime import (
    ConnectionReplaced,
    EffectError,
    EffectFinished,
    EffectId,
    EffectOutcome,
    EventOrigin,
    ExpireEffect,
    Owner,
    RawMpvEvent,
    SendMpvCommand,
    SessionMailbox,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1.0

    def __call__(self) -> float:
        return self.now


class FakeIPC:
    def __init__(self) -> None:
        self.event_sink = None
        self.connection_sink = None
        self.legacy_source = None
        self.requests: list[IPCRequest] = []

    def install_runtime_ingress(self, event_sink, connection_sink, legacy_source, gateway) -> None:
        self.event_sink = event_sink
        self.connection_sink = connection_sink
        self.legacy_source = legacy_source
        self.gateway = gateway

    def command_async(self, *_args, expected_connection_epoch=None) -> IPCRequest:
        if expected_connection_epoch not in {None, 0}:
            return IPCRequest(len(self.requests), 0, Future(), accepted=False)
        request = IPCRequest(len(self.requests), 0, Future())
        self.requests.append(request)
        return request

    def publish(self, message: dict, epoch: int = 0) -> None:
        assert self.event_sink is not None
        self.event_sink(message, epoch)


class ImmediateEventTransport:
    def __init__(self) -> None:
        self._sent = False
        self._closed = threading.Event()

    def read(self, _size: int) -> bytes:
        if not self._sent:
            self._sent = True
            return b'{"event":"file-loaded"}\n'
        self._closed.wait(1)
        return b""

    def write(self, _data: bytes) -> None:
        pass

    def close(self) -> None:
        self._closed.set()


def command(effect_id: int, *, deadline: float = 5.0) -> SendMpvCommand:
    return SendMpvCommand(
        EffectId(effect_id),
        Owner.SESSION,
        f"command:{effect_id}",
        ("get_property", "pause"),
        deadline,
        0,
    )


def test_gateway_publishes_wire_event_before_the_producer_returns() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    MpvGateway(ipc, mailbox)

    ipc.publish({"event": "client-message", "args": ["saitenka-picker"]})

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.origin == EventOrigin.MPV
    assert envelope.payload == RawMpvEvent(
        "client-message", {"event": "client-message", "args": ["saitenka-picker"]}
    )


def test_legacy_router_reads_the_authoritative_mailbox_once() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    MpvGateway(ipc, mailbox)
    event = {"event": "property-change", "name": "sub-text", "data": "猫"}
    ipc.publish(event)

    assert ipc.legacy_source() == [event]
    assert ipc.legacy_source() == []


def test_gateway_preserves_events_buffered_before_installation() -> None:
    ipc = MpvIPC("unused")
    event = {"event": "file-loaded"}
    ipc._feed(b'{"event":"file-loaded"}\n')

    MpvGateway(ipc, SessionMailbox())

    assert ipc.drain_events() == [event]
    ipc.close()


def test_replacement_epoch_is_published_before_its_first_wire_event() -> None:
    mailbox = SessionMailbox()
    ipc = MpvIPC("unused")
    MpvGateway(ipc, mailbox)

    installed, _retired = ipc._install_replacement(ImmediateEventTransport())

    assert installed
    first = mailbox.receive(timeout=1)
    second = mailbox.receive(timeout=1)
    assert first is not None and first.payload == ConnectionReplaced(1)
    assert second is not None and second.payload == RawMpvEvent(
        "file-loaded", {"event": "file-loaded"}
    )
    assert first.sequence < second.sequence
    ipc.close()


def test_ipc_rejects_old_epoch_command_after_replacement_installation() -> None:
    ipc = MpvIPC("unused")
    ipc._connection_epoch = 1
    ipc._closed.clear()

    request = ipc.command_async(
        "get_property",
        "pause",
        expected_connection_epoch=0,
    )

    assert not request.accepted
    assert request.connection_epoch == 1
    assert request.future.result() == {"error": "stale-epoch"}
    assert ipc._pending == {}
    ipc.close()


def test_gateway_reply_completes_the_reserved_runtime_effect() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox, clock=Clock())
    effect = command(1)
    assert mailbox.reserve_terminal(effect.effect_id)
    assert gateway.dispatch(effect)

    ipc.requests[0].future.set_result({"error": "success", "data": True})

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.payload == EffectFinished(
        effect.effect_id,
        effect.owner,
        effect.identity,
        EffectOutcome.SUCCEEDED,
        result=True,
    )
    assert gateway.snapshot.pending == 0


def test_reply_after_recorded_deadline_is_timeout() -> None:
    clock = Clock()
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox, clock=clock)
    effect = command(2, deadline=2.0)
    assert mailbox.reserve_terminal(effect.effect_id)
    assert gateway.dispatch(effect)
    clock.now = 2.1

    ipc.requests[0].future.set_result({"error": "success", "data": True})

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.payload == EffectFinished(
        effect.effect_id,
        effect.owner,
        effect.identity,
        EffectOutcome.FAILED,
        error=EffectError.TIMEOUT,
    )


def test_expiration_wins_once_and_late_reply_is_stale_evidence() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox, clock=Clock())
    effect = command(3)
    assert mailbox.reserve_terminal(effect.effect_id)
    assert gateway.dispatch(effect)

    gateway.expire(ExpireEffect(effect.effect_id, effect.deadline))
    ipc.requests[0].future.set_result({"error": "success", "data": True})

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.payload == EffectFinished(
        effect.effect_id,
        effect.owner,
        effect.identity,
        EffectOutcome.FAILED,
        error=EffectError.TIMEOUT,
    )
    assert mailbox.receive(timeout=0) is None
    assert gateway.snapshot.stale_outcomes == 1


def test_gateway_rejects_command_for_an_old_connection_epoch() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox)
    assert ipc.connection_sink is not None
    ipc.connection_sink("replaced", 2)

    assert not gateway.dispatch(command(4))


def test_gateway_turns_inbound_overload_into_controlled_legacy_stop() -> None:
    mailbox = SessionMailbox(normal_capacity=1)
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox)
    ipc.publish({"event": "property-change", "name": "pause", "data": True})

    ipc.publish({"event": "property-change", "name": "sub-text", "data": "猫"})

    try:
        ipc.legacy_source()
    except OSError as error:
        assert str(error) == "runtime mailbox overloaded"
    else:  # pragma: no cover - bounded ingress contract
        raise AssertionError("mailbox overload did not stop the legacy session")
    assert gateway.snapshot.inbound_overloads == 1
