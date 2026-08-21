from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from typing import TYPE_CHECKING, cast

from saitenka.mpvio.gateway import MpvGateway
from saitenka.mpvio.ipc import IPCRequest, MpvIPC
from saitenka.runtime.effects import EffectError, EffectOutcome, Owner
from saitenka.runtime.events import PropertyObserved
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
        self.session_loop = None
        self.event_sink = None

    def install_runtime_ingress(self, event_sink, _connection_sink, session_loop, _gateway):
        self.session_loop = session_loop
        self.event_sink = event_sink

    def command_async(self, *args, expected_connection_epoch=None) -> IPCRequest:
        del args, expected_connection_epoch
        request = IPCRequest(len(self.requests), 0, Future())
        self.requests.append(request)
        return request

    def drain(self, timeout: float | None = 0.0) -> list:
        """One turn as a list — the loop pushes, so a test that wants a batch collects it."""
        events: list = []
        self.session_loop.receive(timeout, events.append)
        return events


def test_legacy_bridge_delivers_a_command_outcome_on_the_reader_turn() -> None:
    ipc = FakeIPC()
    mailbox = SessionMailbox()
    gateway = MpvGateway(cast("MpvIPC", ipc), mailbox, clock=Clock())
    completed: list[EffectFinished] = []

    assert gateway.correlator.submit_mpv(
        owner=Owner.SESSION,
        identity="startup-hint",
        command=("show-text", "starting", 30000),
        timeout_s=10.0,
        on_finished=completed.append,
    )
    ipc.requests[0].future.set_result({"error": "success", "data": True})
    assert completed == []

    assert ipc.drain() == []
    assert len(completed) == 1
    assert completed[0].outcome is EffectOutcome.SUCCEEDED
    assert completed[0].result is True


def test_legacy_bridge_deadline_wins_and_late_reply_is_stale() -> None:
    clock = Clock()
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=clock)
    completed: list[EffectFinished] = []
    assert gateway.correlator.submit_mpv(
        owner=Owner.SESSION,
        identity="startup-hint",
        command=("show-text", "starting", 30000),
        timeout_s=1.0,
        on_finished=completed.append,
    )

    clock.now = 1.0
    assert ipc.drain() == []
    assert ipc.drain() == []
    ipc.requests[0].future.set_result({"error": "success"})
    assert ipc.drain() == []

    assert len(completed) == 1
    assert completed[0].outcome is EffectOutcome.FAILED
    assert completed[0].error is EffectError.TIMEOUT
    assert gateway.snapshot.stale_outcomes == 1


def test_legacy_bridge_rejects_when_terminal_capacity_cannot_fit_the_deadline_pair() -> None:
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(terminal_capacity=1), clock=Clock())
    completed: list[EffectFinished] = []

    assert not gateway.correlator.submit_mpv(
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

    assert gateway.correlator.schedule_timer(
        owner=Owner.SESSION,
        identity="toast:1",
        timer="lifecycle:toast",
        due_at=2.0,
        on_finished=completed.append,
    )
    clock.now = 1.99
    assert ipc.drain() == []
    assert completed == []

    clock.now = 2.0
    assert ipc.drain() == []
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

    assert gateway.correlator.schedule_timer(
        owner=Owner.SESSION,
        identity="toast:1",
        timer="lifecycle:toast",
        due_at=10.25,
        on_finished=lambda _result: None,
    )
    ipc.drain(30.0)

    assert waits == [0.25]  # the timer, not the caller's thirty seconds


def test_the_drain_blocks_for_as_long_as_asked_when_nothing_is_armed() -> None:
    """The negative control: bounding unconditionally would turn every idle wait into a spin."""
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), _RecordingMailbox(), clock=Clock())
    waits = gateway.mailbox.waits

    ipc.drain(30.0)
    ipc.drain(None)

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
            gateway.correlator.schedule_timer(
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
        assert gateway.correlator.schedule_timer(
            owner=Owner.SESSION,
            identity=identity,
            timer="lifecycle:toast",
            due_at=due,
            on_finished=lambda result, identity=identity: completed.append(
                (identity, result.outcome)
            ),
        )
    assert ipc.drain() == []
    clock.now = 2.0
    assert ipc.drain() == []

    assert completed == [
        ("toast:1", EffectOutcome.CANCELLED),
        ("toast:2", EffectOutcome.SUCCEEDED),
    ]


def test_named_timer_rejects_when_terminal_capacity_is_full() -> None:
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(terminal_capacity=1), clock=Clock())
    completed: list[EffectFinished] = []
    assert gateway.correlator.schedule_timer(
        owner=Owner.SESSION,
        identity="first",
        timer="first",
        due_at=1.0,
        on_finished=completed.append,
    )

    assert not gateway.correlator.schedule_timer(
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
    gateway.correlator.handle_terminal(cast("EffectFinished", envelope.payload))
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
    assert ipc.drain() == []
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
    assert gateway.correlator.submit_mpv(
        owner=Owner.SESSION,
        identity="probe",
        command=("get_property", "sub-visibility"),
        timeout_s=10.0,
        on_finished=lambda completion: sink.append(("terminal", completion.identity)),
    )
    assert ipc.event_sink is not None
    ipc.event_sink({"event": "property-change", "name": "sub-text", "data": "ichi"}, 0)
    ipc.requests[0].future.set_result({"error": "success", "data": True})


def test_a_completion_is_dispatched_at_its_place_in_the_sequence() -> None:
    """A completion published after an observation runs after it — the whole of what the retired
    ordered/inline modes were arguing about.

    They existed because the consumer received a batch and only then acted: a callback run during
    the receive preceded every observation the consumer had not reached yet, and handing it back
    instead just moved the question to who dispatches it. With each payload handed over as it is
    popped, "in position" and "now" are the same instant and there is nothing left to choose.
    """
    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=Clock())
    seen: list = []

    def record(event: object) -> None:
        assert isinstance(event, PropertyObserved)
        seen.append(("observation", event.name))

    _observation_then_terminal(ipc, gateway, seen)
    assert ipc.session_loop is not None
    ipc.session_loop.receive(0.0, record)

    assert seen == [("observation", "sub-text"), ("terminal", "probe")]


def test_a_claimed_batch_still_completes_the_effect_it_carried() -> None:
    """The completion belongs to the correlator, so claiming the observations around it changes
    who sees the batch and not whether the effect finishes."""
    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    gateway = MpvGateway(cast("MpvIPC", ipc), SessionMailbox(), clock=Clock())
    install_session_reactor(gateway, startup_hint=False)
    seen: list = []

    _observation_then_terminal(ipc, gateway, seen)
    drained = ipc.drain()

    assert seen == [("terminal", "probe")]
    assert drained == [], "the observation was claimed and the completion never left the receive"
