from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import pytest

from saitenka.mpvio.gateway import MpvGateway
from saitenka.mpvio.ipc import IPCRequest, MpvIPC
from saitenka.runtime import (
    CommandHandled,
    CommandOutcome,
    CommandReason,
    ConnectionLost,
    ConnectionReady,
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
    SurfaceAction,
    SurfaceTransaction,
    TrafficClass,
    UserCommand,
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
        self.commands: list[tuple] = []
        self.reconnect_results: list[bool] = []
        self.reconnects_left = 3
        self.disconnected = False
        self.reconnected = threading.Event()
        self.replay_entered: threading.Event | None = None
        self.replay_release: threading.Event | None = None

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

    def command(self, *args, **_kwargs) -> dict:
        self.commands.append(args)
        if args and args[0] == "observe_property" and self.replay_entered is not None:
            self.replay_entered.set()
            assert self.replay_release is not None
            assert self.replay_release.wait(1)
        return {"error": "success", "data": args[-1]}

    def reconnect_once(self) -> bool:
        result = self.reconnect_results.pop(0) if self.reconnect_results else False
        self.reconnects_left -= 1
        if result:
            self.disconnected = False
            assert self.connection_sink is not None
            self.connection_sink("replaced", 1)
            self.reconnected.set()
        return result

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


def surface_command(effect_id: int, revision: int) -> SendMpvCommand:
    return SendMpvCommand(
        EffectId(effect_id),
        Owner.PRESENTATION,
        SurfaceTransaction("toast", revision, SurfaceAction.PRESENT),
        ("overlay-add", 2, "pixels"),
        5.0,
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
    assert envelope.payload == UserCommand("saitenka-picker", command_id=0)


def test_gateway_preserves_typed_command_arguments() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    MpvGateway(ipc, mailbox)

    ipc.publish({"event": "client-message", "args": ["saitenka-command", 1, "two"]})

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.payload == UserCommand("saitenka-command", (1, "two"), 0)


@pytest.mark.timeout(5)
def test_connection_loss_wakes_reconnect_and_replays_registered_observers() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    ipc.reconnect_results = [True]
    gateway = MpvGateway(ipc, mailbox)
    assert gateway.register_observers(("pause",)) == {
        "pause": {"error": "success", "data": "pause"}
    }
    ipc.commands.clear()
    assert ipc.connection_sink is not None

    ipc.connection_sink("lost", 0)
    assert ipc.reconnected.wait(1)
    deadline = time.monotonic() + 1
    events: list[object] = []
    while time.monotonic() < deadline and len(events) < 5:
        events.extend(ipc.legacy_source())
        time.sleep(0.001)

    assert events == [
        ConnectionLost(0),
        ConnectionReplaced(1),
        {"event": "property-change", "name": "pause", "data": "pause"},
        {"event": "file-loaded"},
        ConnectionReady(1),
    ]
    assert ipc.commands == [
        ("observe_property", 1, "pause"),
        ("get_property", "pause"),
    ]


@pytest.mark.timeout(5)
def test_failed_reconnect_retries_only_when_named_timer_is_due() -> None:
    clock = Clock()
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    ipc.reconnect_results = [False, True]
    MpvGateway(ipc, mailbox, clock=clock)
    assert ipc.connection_sink is not None

    ipc.connection_sink("lost", 0)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and (
        len(ipc.reconnect_results) == 2 or mailbox.snapshot.terminal_reserved == 0
    ):
        time.sleep(0.001)
    assert len(ipc.reconnect_results) == 1
    assert not ipc.reconnected.is_set()

    ipc.legacy_source()
    assert not ipc.reconnected.is_set()
    clock.now += 0.05
    ipc.legacy_source()
    assert ipc.reconnected.wait(1)


def test_gateway_represents_malformed_client_message_for_policy_rejection() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    MpvGateway(ipc, mailbox)

    ipc.publish({"event": "client-message", "args": []})

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.payload == UserCommand("", command_id=0)


def test_legacy_router_reads_the_authoritative_mailbox_once() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    MpvGateway(ipc, mailbox)
    event = {"event": "property-change", "name": "sub-text", "data": "猫"}
    ipc.publish(event)

    assert ipc.legacy_source() == [event]
    assert ipc.legacy_source() == []


def test_legacy_router_preserves_typed_user_command() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    MpvGateway(ipc, mailbox)
    ipc.publish({"event": "client-message", "args": ["saitenka-picker", "arg"]})

    assert ipc.legacy_source() == [UserCommand("saitenka-picker", ("arg",), 0)]


def test_gateway_publishes_legacy_command_terminal_outcome() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox)
    ipc.publish({"event": "client-message", "args": ["mine"]})
    assert mailbox.receive(timeout=0) is not None
    outcome = CommandHandled(
        "mine",
        Owner.INTERACTION,
        CommandOutcome.FAILED,
        command_id=0,
        reason=CommandReason.INTERNAL,
    )

    gateway.publish_legacy_outcome(outcome)

    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.origin == EventOrigin.USER
    assert envelope.payload == outcome


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (CommandOutcome.EXECUTED, None),
        (CommandOutcome.FAILED, CommandReason.INTERNAL),
        (CommandOutcome.SUPPRESSED, CommandReason.LEGACY_REPEAT),
    ],
)
def test_command_terminal_slot_survives_normal_lane_saturation(outcome, reason) -> None:
    mailbox = SessionMailbox(normal_capacity=2)
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox)
    ipc.publish({"event": "client-message", "args": ["mine"]})
    assert mailbox.receive(timeout=0) is not None
    mailbox.publish(
        RawMpvEvent("property-change"),
        origin=EventOrigin.MPV,
        traffic=TrafficClass.NORMAL,
    )

    terminal = CommandHandled("mine", Owner.INTERACTION, outcome, command_id=0, reason=reason)
    gateway.publish_legacy_outcome(terminal)

    assert mailbox.snapshot.command_reserved == 0
    assert [envelope.payload for envelope in mailbox.receive_ready()] == [
        RawMpvEvent("property-change"),
        terminal,
    ]


def test_gateway_preserves_events_buffered_before_installation() -> None:
    ipc = MpvIPC("unused")
    event = {"event": "file-loaded"}
    ipc._feed(b'{"event":"file-loaded"}\n')

    MpvGateway(ipc, SessionMailbox())

    assert ipc.drain_events() == [event]
    ipc.close()


def test_gateway_observes_a_disconnect_that_preceded_ingress_installation() -> None:
    mailbox = SessionMailbox()
    ipc = MpvIPC("unused")
    ipc._closed.set()
    gateway = MpvGateway(ipc, mailbox)

    envelope = mailbox.receive(timeout=1)

    assert envelope is not None
    assert envelope.payload == ConnectionLost(0)
    gateway.close()
    ipc.close()


@pytest.mark.timeout(5)
def test_exhausted_reconnect_requests_a_bounded_session_stop() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    ipc.reconnect_results = [False]
    ipc.reconnects_left = 1
    MpvGateway(ipc, mailbox)
    assert ipc.connection_sink is not None

    ipc.connection_sink("lost", 0)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            ipc.legacy_source()
        except OSError as error:
            assert str(error) == "mpv-disconnected"
            return
        time.sleep(0.001)
    raise AssertionError("reconnect exhaustion did not stop the session")


def test_closed_gateway_ignores_late_connection_loss() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox)
    assert ipc.connection_sink is not None
    gateway.close()

    ipc.connection_sink("lost", 0)

    assert mailbox.receive(timeout=0) is None


def test_candidate_wire_event_is_not_published_before_replay_commits() -> None:
    mailbox = SessionMailbox()
    ipc = MpvIPC("unused")
    MpvGateway(ipc, mailbox)

    installed, _retired = ipc._install_replacement(ImmediateEventTransport())

    assert installed
    assert mailbox.receive(timeout=0) is None
    ipc.close()


@pytest.mark.timeout(5)
def test_replaying_connection_blocks_commands_and_buffers_wire_events() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    ipc.reconnect_results = [True]
    gateway = MpvGateway(ipc, mailbox)
    gateway.register_observers(("pause",))
    ipc.replay_entered = threading.Event()
    ipc.replay_release = threading.Event()
    assert ipc.connection_sink is not None

    ipc.connection_sink("lost", 0)
    assert ipc.replay_entered.wait(1)
    ipc.publish({"event": "property-change", "name": "pause", "data": False}, epoch=1)
    blocked = SendMpvCommand(
        EffectId(71),
        Owner.SESSION,
        "during-replay",
        ("set_property", "pause", True),
        5.0,
        1,
    )
    assert not gateway.dispatch(blocked)
    assert ipc.legacy_source() == [ConnectionLost(0)]

    ipc.replay_release.set()
    deadline = time.monotonic() + 1
    events: list[object] = []
    while time.monotonic() < deadline and not any(isinstance(x, ConnectionReady) for x in events):
        events.extend(ipc.legacy_source())
        time.sleep(0.001)

    assert events == [
        ConnectionReplaced(1),
        {"event": "property-change", "name": "pause", "data": "pause"},
        {"event": "file-loaded"},
        {"event": "property-change", "name": "pause", "data": False},
        ConnectionReady(1),
    ]


@pytest.mark.timeout(5)
def test_failed_candidate_never_publishes_a_replacement() -> None:
    class FailedCandidateIPC(FakeIPC):
        def reconnect_once(self) -> bool:
            self.reconnects_left = 0
            self.disconnected = True
            assert self.connection_sink is not None
            self.connection_sink("replaced", 1)
            self.publish({"event": "file-loaded"}, epoch=1)
            return False

    mailbox = SessionMailbox()
    ipc = FailedCandidateIPC()
    MpvGateway(ipc, mailbox)
    assert ipc.connection_sink is not None

    ipc.connection_sink("lost", 0)
    deadline = time.monotonic() + 1
    observed: list[object] = []
    while time.monotonic() < deadline:
        try:
            observed.extend(ipc.legacy_source())
        except OSError:
            break
        time.sleep(0.001)

    assert not any(isinstance(event, ConnectionReplaced | ConnectionReady) for event in observed)


@pytest.mark.timeout(5)
def test_loss_after_commit_before_worker_cleanup_starts_another_attempt(monkeypatch) -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    ipc.reconnect_results = [True, False]
    gateway = MpvGateway(ipc, mailbox)
    committed = threading.Event()
    release_cleanup = threading.Event()
    original_commit = gateway._commit_replacement

    def commit_then_block(connection_epoch: int, replay: tuple[dict, ...]) -> bool:
        result = original_commit(connection_epoch, replay)
        committed.set()
        assert release_cleanup.wait(1)
        return result

    monkeypatch.setattr(gateway, "_commit_replacement", commit_then_block)
    assert ipc.connection_sink is not None

    ipc.connection_sink("lost", 0)
    assert committed.wait(1)
    ipc.connection_sink("lost", 1)
    release_cleanup.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and ipc.reconnect_results:
        time.sleep(0.001)

    assert ipc.reconnect_results == []


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


def test_healthy_writer_lock_contention_does_not_drop_command() -> None:
    ipc = MpvIPC("unused")
    ipc._closed.clear()
    entered = threading.Event()
    requests: list[IPCRequest] = []

    ipc._write_lock.acquire()
    thread = threading.Thread(
        target=lambda: (entered.set(), requests.append(ipc.command_async("show-text", "ok", 1)))
    )
    thread.start()
    assert entered.wait(1)
    assert requests == []
    ipc._write_lock.release()
    thread.join(1)

    assert requests and requests[0].accepted
    assert requests[0].connection_epoch == 0
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


def test_gateway_supersedes_older_surface_revision_before_wire_write() -> None:
    mailbox = SessionMailbox()
    ipc = FakeIPC()
    gateway = MpvGateway(ipc, mailbox, clock=Clock())
    newer = surface_command(10, 2)
    older = surface_command(11, 1)
    assert mailbox.reserve_terminal(newer.effect_id)
    assert mailbox.reserve_terminal(older.effect_id)

    assert gateway.dispatch(newer)
    assert gateway.dispatch(older)

    assert len(ipc.requests) == 1
    envelope = mailbox.receive(timeout=0)
    assert envelope is not None
    assert envelope.payload == EffectFinished(
        older.effect_id,
        older.owner,
        older.identity,
        EffectOutcome.SUPERSEDED,
    )


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


def test_a_correlated_write_reaches_the_wire_before_a_following_read() -> None:
    """The premise `send_correlated` is built on, and which every non-awaited caller depends on:
    the write is dispatched during the submit, not queued for the reactor, so a synchronous readback
    right after it observes it. If it were deferred, `_add_and_select` would be followed by a
    track-list read that cannot yet see the track it just added.
    """
    from saitenka.app.mpv_egress import send_correlated
    from saitenka.runtime import Owner

    order: list[str] = []

    class Ledger(FakeIPC):
        def command_async(self, *args, **kwargs) -> IPCRequest:
            order.append(f"write:{args[0]}")
            return super().command_async(*args, **kwargs)

        def command(self, *args, **kwargs) -> dict:
            order.append(f"read:{args[1]}")
            return super().command(*args, **kwargs)

    ipc = Ledger()
    gateway = MpvGateway(ipc, SessionMailbox())
    ipc.submit_runtime_mpv = gateway.submit_mpv  # type: ignore[attr-defined]

    send_correlated(ipc, "sub-add-select", "sub-add", "/ep.srt", "select", owner=Owner.SUBTITLE)
    ipc.command("get_property", "track-list")

    assert order == ["write:sub-add", "read:track-list"]
