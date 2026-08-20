from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from typing import TYPE_CHECKING, cast

from saitenka.mpvio.gateway import MpvGateway
from saitenka.mpvio.ipc import IPCRequest, MpvIPC
from saitenka.runtime.effects import EffectError, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy
from saitenka.runtime.mailbox import SessionMailbox

if TYPE_CHECKING:
    from saitenka.runtime.events import EffectFinished


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class _RecordingMailbox(SessionMailbox):
    """A mailbox that records what each `receive` was told to wait for.

    The bound is not observable from outside without a real clock to measure against, and a real
    clock in a test is the flake this suite avoids everywhere else.
    """

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float | None] = []

    def receive(self, *, timeout: float | None = None):
        self.waits.append(timeout)


class FakeIPC:
    def __init__(self) -> None:
        self.requests: list[IPCRequest] = []
        self.legacy_source = list
        self.event_sink = None

    def install_runtime_ingress(self, event_sink, _connection_sink, legacy_source, _gateway):
        self.legacy_source = legacy_source
        self.event_sink = event_sink

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


def test_named_timer_delivers_only_after_its_due_turn() -> None:
    clock = Clock()
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=clock)
    completed: list[EffectFinished] = []

    assert gateway.legacy.schedule_timer(
        owner=Owner.SESSION,
        identity="toast:1",
        timer="lifecycle:toast",
        due_at=2.0,
        on_finished=completed.append,
    )
    clock.now = 1.99
    assert ipc.legacy_source() == []
    assert completed == []

    clock.now = 2.0
    assert ipc.legacy_source() == []
    assert [item.outcome for item in completed] == [EffectOutcome.SUCCEEDED]


def test_the_drain_never_blocks_past_the_earliest_armed_timer() -> None:
    """The wake bound the retired poll interval used to supply, from the thing that knows it.

    A timer that produces no mpv event is invisible to the mailbox, so a drain bounded only by its
    caller's timeout sleeps straight through the deadline. With no tick left to hide that, the
    earliest armed timer is the bound.
    """
    clock = Clock()
    clock.now = 10.0
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), _RecordingMailbox(), clock=clock)
    waits = gateway.mailbox.waits

    assert gateway.legacy.schedule_timer(
        owner=Owner.SESSION,
        identity="toast:1",
        timer="lifecycle:toast",
        due_at=10.25,
        on_finished=lambda _result: None,
    )
    ipc.legacy_source(30.0)

    assert waits == [0.25]  # the timer, not the caller's thirty seconds


def test_the_drain_blocks_for_as_long_as_asked_when_nothing_is_armed() -> None:
    """The negative control: bounding unconditionally would turn every idle wait into a spin."""
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), _RecordingMailbox(), clock=Clock())
    waits = gateway.mailbox.waits

    ipc.legacy_source(30.0)
    ipc.legacy_source(None)

    assert waits == [30.0, None]  # unbounded stays unbounded


def test_arming_a_timer_releases_a_receiver_already_blocked_under_a_later_bound() -> None:
    """Arming from another thread has to reach a wait that was bounded before it existed.

    Otherwise the new deadline is only honoured from the next turn, which for a session blocked
    with nothing else armed is never — the same hang the stop flag needed the wake for.
    """
    clock = Clock()
    ipc = FakeIPC()
    mailbox = SessionMailbox()
    gateway = MpvGateway(cast("MpvIPC", ipc), mailbox, clock=clock)
    released = threading.Event()

    def receiver() -> None:
        mailbox.receive(timeout=5.0)
        released.set()

    thread = threading.Thread(target=receiver)
    thread.start()
    try:
        deadline = time.monotonic() + 2.0
        while not released.is_set() and time.monotonic() < deadline:
            # Re-armed against a deadline for the reason the stop wake is: the release is
            # un-latched, so one sent before the thread blocks is correctly a no-op.
            gateway.legacy.schedule_timer(
                owner=Owner.SESSION,
                identity="late",
                timer="lifecycle:late",
                due_at=1.0,
                on_finished=lambda _result: None,
            )
            released.wait(0.01)
        assert released.is_set()
    finally:
        thread.join(2.0)


def test_replacing_named_timer_terminally_cancels_old_revision() -> None:
    clock = Clock()
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=clock)
    completed: list[tuple[str, EffectOutcome]] = []

    for identity, due in (("toast:1", 1.0), ("toast:2", 2.0)):
        assert gateway.legacy.schedule_timer(
            owner=Owner.SESSION,
            identity=identity,
            timer="lifecycle:toast",
            due_at=due,
            on_finished=lambda result, identity=identity: completed.append(
                (identity, result.outcome)
            ),
        )
    assert ipc.legacy_source() == []
    clock.now = 2.0
    assert ipc.legacy_source() == []

    assert completed == [
        ("toast:1", EffectOutcome.CANCELLED),
        ("toast:2", EffectOutcome.SUCCEEDED),
    ]


def test_named_timer_rejects_when_terminal_capacity_is_full() -> None:
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(terminal_capacity=1), clock=Clock())
    completed: list[EffectFinished] = []
    assert gateway.legacy.schedule_timer(
        owner=Owner.SESSION,
        identity="first",
        timer="first",
        due_at=1.0,
        on_finished=completed.append,
    )

    assert not gateway.legacy.schedule_timer(
        owner=Owner.SESSION,
        identity="second",
        timer="second",
        due_at=1.0,
        on_finished=completed.append,
    )

    assert len(completed) == 1
    assert completed[0].identity == "second"
    assert completed[0].outcome is EffectOutcome.REJECTED
    assert completed[0].error is EffectError.OVERLOADED


def test_job_completion_is_delivered_on_the_reader_turn() -> None:
    ipc = FakeIPC()
    mailbox = SessionMailbox()
    gateway = MpvGateway(cast("MpvIPC", ipc), mailbox, clock=Clock())
    ran = threading.Event()
    completed: list[EffectFinished] = []

    def work(request: object, _cancelled: threading.Event) -> object:
        ran.set()
        return f"done:{request}"

    gateway.register_job_lane("probe", JobLanePolicy(capacity=1), work)
    assert gateway.submit_job(
        owner=Owner.SESSION,
        identity="probe:1",
        lane="probe",
        request="input",
        on_finished=completed.append,
    )
    assert ran.wait(1.0)
    assert completed == []

    envelope = mailbox.receive(timeout=1.0)
    assert envelope is not None
    gateway.legacy.handle_terminal(cast("EffectFinished", envelope.payload))
    assert len(completed) == 1
    assert completed[0].outcome is EffectOutcome.SUCCEEDED
    assert completed[0].result == "done:input"


def test_job_lane_rejects_saturation_with_a_terminal_outcome() -> None:
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=Clock())
    started = threading.Event()
    release = threading.Event()
    completed: list[EffectFinished] = []

    def blocked(request: object, _cancelled: threading.Event) -> object:
        started.set()
        release.wait()
        return request

    gateway.register_job_lane("probe", JobLanePolicy(capacity=1), blocked)
    assert gateway.submit_job(
        owner=Owner.SESSION,
        identity="probe:1",
        lane="probe",
        request="first",
        on_finished=completed.append,
    )
    assert started.wait(1.0)
    assert not gateway.submit_job(
        owner=Owner.SESSION,
        identity="probe:2",
        lane="probe",
        request="second",
        on_finished=completed.append,
    )
    assert len(completed) == 1
    assert completed[0].identity == "probe:2"
    assert completed[0].outcome is EffectOutcome.REJECTED
    assert completed[0].error is EffectError.OVERLOADED
    release.set()


def test_job_close_cancels_pending_work_and_quarantines_late_completion() -> None:
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=Clock())
    started = threading.Event()
    completed: list[EffectFinished] = []

    def cooperative(request: object, cancelled: threading.Event) -> object:
        started.set()
        assert cancelled.wait(1.0)
        return request

    gateway.register_job_lane("probe", JobLanePolicy(capacity=1), cooperative)
    assert gateway.submit_job(
        owner=Owner.SESSION,
        identity="probe:1",
        lane="probe",
        request="input",
        on_finished=completed.append,
    )
    assert started.wait(1.0)

    gateway.close()
    assert ipc.legacy_source() == []
    assert len(completed) == 1
    assert completed[0].outcome is EffectOutcome.CANCELLED
    assert not gateway.submit_job(
        owner=Owner.SESSION,
        identity="probe:2",
        lane="probe",
        request="late",
        on_finished=completed.append,
    )
    assert completed[-1].outcome is EffectOutcome.REJECTED


def _observation_then_terminal(ipc: FakeIPC, gateway: MpvGateway, sink: list) -> None:
    """Publish an observation, then resolve a command so its terminal is the later envelope."""
    assert gateway.legacy.submit_mpv(
        owner=Owner.SESSION,
        identity="probe",
        command=("get_property", "sub-visibility"),
        timeout_s=10.0,
        on_finished=lambda completion: sink.append(("terminal", completion.identity)),
    )
    assert ipc.event_sink is not None
    ipc.event_sink({"event": "property-change", "name": "sub-text", "data": "ichi"}, 0)
    ipc.requests[0].future.set_result({"error": "success", "data": True})


def test_ordered_terminals_hands_the_completion_back_in_envelope_sequence() -> None:
    """A completion published after an observation must not be dispatched before it. The caller
    owns a whole turn, so it receives both and runs them in the order mpv produced them."""
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=Clock())
    seen: list = []

    _observation_then_terminal(ipc, gateway, seen)
    drained = ipc.legacy_source(ordered_terminals=True)

    assert seen == []  # nothing ran during the drain
    for event in drained:
        if isinstance(event, dict):
            seen.append(("observation", event["name"]))
        else:
            gateway.dispatch_terminal(event)
    assert seen == [("observation", "sub-text"), ("terminal", "probe")]


def test_inline_dispatch_runs_the_completion_before_the_batch_is_handled() -> None:
    """The negative control for the test above, and the contract `_drive_annotation_once` keeps:
    without ordered terminals the callback fires mid-drain, ahead of an older observation."""
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=Clock())
    seen: list = []

    _observation_then_terminal(ipc, gateway, seen)
    drained = ipc.legacy_source()

    assert seen == [("terminal", "probe")]  # already ran, before the caller sees anything
    assert [event["name"] for event in drained if isinstance(event, dict)] == ["sub-text"]
