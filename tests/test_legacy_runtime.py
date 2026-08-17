from __future__ import annotations

from concurrent.futures import Future
from typing import TYPE_CHECKING, cast

from saitenka.mpvio.gateway import MpvGateway
from saitenka.mpvio.ipc import IPCRequest, MpvIPC
from saitenka.runtime.effects import EffectError, EffectOutcome, Owner
from saitenka.runtime.mailbox import SessionMailbox

if TYPE_CHECKING:
    from saitenka.runtime.events import EffectFinished


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeIPC:
    def __init__(self) -> None:
        self.requests: list[IPCRequest] = []
        self.legacy_source = list

    def install_runtime_ingress(self, _event_sink, _connection_sink, legacy_source, _gateway):
        self.legacy_source = legacy_source

    def command_async(self, *args, expected_connection_epoch=None) -> IPCRequest:
        del args, expected_connection_epoch
        request = IPCRequest(len(self.requests), 0, Future())
        self.requests.append(request)
        return request


def test_legacy_bridge_delivers_a_command_outcome_on_the_reader_turn() -> None:
    ipc = FakeIPC()
    mailbox = SessionMailbox()
    gateway = MpvGateway(cast("MpvIPC", ipc), mailbox, clock=Clock())
    completed: list[EffectFinished] = []

    assert gateway.legacy.submit_mpv(
        owner=Owner.SESSION,
        identity="startup-hint",
        command=("show-text", "starting", 30000),
        timeout_s=10.0,
        on_finished=completed.append,
    )
    ipc.requests[0].future.set_result({"error": "success", "data": True})
    assert completed == []

    assert ipc.legacy_source() == []
    assert len(completed) == 1
    assert completed[0].outcome is EffectOutcome.SUCCEEDED
    assert completed[0].result is True


def test_legacy_bridge_deadline_wins_and_late_reply_is_stale() -> None:
    clock = Clock()
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=clock)
    completed: list[EffectFinished] = []
    assert gateway.legacy.submit_mpv(
        owner=Owner.SESSION,
        identity="startup-hint",
        command=("show-text", "starting", 30000),
        timeout_s=1.0,
        on_finished=completed.append,
    )

    clock.now = 1.0
    assert ipc.legacy_source() == []
    assert ipc.legacy_source() == []
    ipc.requests[0].future.set_result({"error": "success"})
    assert ipc.legacy_source() == []

    assert len(completed) == 1
    assert completed[0].outcome is EffectOutcome.FAILED
    assert completed[0].error is EffectError.TIMEOUT
    assert gateway.snapshot.stale_outcomes == 1


def test_legacy_bridge_rejects_when_terminal_capacity_cannot_fit_the_deadline_pair() -> None:
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(terminal_capacity=1), clock=Clock())
    completed: list[EffectFinished] = []

    assert not gateway.legacy.submit_mpv(
        owner=Owner.SESSION,
        identity="startup-hint",
        command=("show-text", "starting", 30000),
        timeout_s=1.0,
        on_finished=completed.append,
    )
    assert len(completed) == 1
    assert completed[0].owner is Owner.SESSION
    assert completed[0].identity == "startup-hint"
    assert completed[0].outcome is EffectOutcome.REJECTED
    assert completed[0].error is EffectError.OVERLOADED
    assert ipc.requests == []
