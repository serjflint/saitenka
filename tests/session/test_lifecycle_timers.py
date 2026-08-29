from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from session_builder import build_session

from saitenka.app.lifecycle_timers import LifecycleTimerKind, LifecycleTimers
from saitenka.app.toast_controller import ToastController
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

if TYPE_CHECKING:
    from saitenka.app.features.mining.mining_controller import MiningController


@dataclass
class _Scheduled:
    identity: object
    callback: object


class FakeTimerPort:
    def __init__(self) -> None:
        self.scheduled: dict[str, _Scheduled] = {}
        self.history: list[_Scheduled] = []

    def schedule_runtime_timer(self, *, identity, timer, on_finished, **_kwargs) -> bool:
        item = _Scheduled(identity, on_finished)
        self.scheduled[timer] = item
        self.history.append(item)
        return True

    def cancel_runtime_timer(self, timer: str) -> bool:
        return self.scheduled.pop(timer, None) is not None

    @staticmethod
    def finish(item: _Scheduled, outcome: EffectOutcome = EffectOutcome.SUCCEEDED) -> None:
        item.callback(EffectFinished(EffectId(1), Owner.SESSION, item.identity, outcome))


class _ProfileMining:
    def select_mining_spec(self, _spec) -> None:
        pass

    def close_capability(self) -> None:
        pass

    def clear_mining_target(self, _identity) -> None:
        pass

    def publish_mining_target(self, _target) -> None:
        pass


def _profile_session(ipc, port: FakeTimerPort):
    from saitenka.app.features.mining.mining_controller import MiningIdentity
    from saitenka.app.features.profiles.profile_controller import (
        ProfileAftermath,
        ProfileController,
        ProfileInvalidation,
        ProfileSubtitles,
    )
    from saitenka.app.features.profiles.profile_session import (
        ProfileDependencyPorts,
        ProfileSession,
        ProfileSessionAssembly,
    )
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.profiles import DEFAULT_PROFILE
    from saitenka.mpvio.osd import Overlay

    def nothing() -> None:
        pass

    profile = ProfileController(
        DEFAULT_PROFILE,
        None,
        ProfileInvalidation(nothing, nothing, nothing),
        ProfileSubtitles(lambda: "ja", lambda _slang: False, lambda _slang: None, nothing),
        ProfileAftermath(nothing, lambda _text, _kind: None),
    )
    return ProfileSession(
        ProfileSessionAssembly(
            profile,
            cast("MiningController", _ProfileMining()),
            LifecycleTimers(port),
            LifecycleSurfaces(Overlay(ipc)),
            ProfileDependencyPorts(nothing, nothing, lambda: 0, nothing),
            lambda: 0,
            lambda _mode, _workers: None,
        ),
        identity=MiningIdentity(DEFAULT_PROFILE.name, 0),
        scorer=None,
    )


def test_replaced_lifecycle_timer_cannot_apply_after_new_revision() -> None:
    port = FakeTimerPort()
    timers = LifecycleTimers(port, clock=lambda: 10.0)
    applied: list[str] = []
    timers.schedule(LifecycleTimerKind.TOAST_EXPIRY, 1.0, lambda: applied.append("old"))
    old = port.history[-1]
    timers.schedule(LifecycleTimerKind.TOAST_EXPIRY, 2.0, lambda: applied.append("new"))
    new = port.history[-1]

    port.finish(old)
    port.finish(new)

    assert applied == ["new"]


def test_cancelled_lifecycle_timer_cannot_apply_late() -> None:
    port = FakeTimerPort()
    timers = LifecycleTimers(port)
    applied: list[bool] = []
    timers.schedule(LifecycleTimerKind.LOADING_FRAME, 0.08, lambda: applied.append(True))
    pending = port.history[-1]

    assert timers.cancel(LifecycleTimerKind.LOADING_FRAME)
    port.finish(pending)

    assert applied == []


def test_toast_expiry_removes_the_lifecycle_surface() -> None:
    from util import FakeIPC

    from saitenka.app.overlay_ids import OverlayId

    ipc = FakeIPC()
    reader = build_session(ipc)
    port = FakeTimerPort()
    reader.turn.notifications = ToastController(
        reader.turn.lifecycle_surfaces,
        reader.turn.screen,
        LifecycleTimers(port),
    )

    reader.turn.toast("saved", seconds=1.0)
    port.finish(port.history[-1])

    assert ("overlay-remove", OverlayId.TOAST) in ipc.commands


def test_loading_frames_are_timer_driven_and_stop_when_loading_finishes() -> None:
    from util import FakeIPC

    from saitenka.app.overlay_ids import OverlayId

    ipc = FakeIPC()
    port = FakeTimerPort()
    profile = _profile_session(ipc, port)
    profile.begin_loading()

    port.finish(port.history[-1])
    assert any(command[:2] == ("overlay-add", OverlayId.LOADING) for command in ipc.commands)
    assert len(port.history) == 2

    from saitenka.app.features.profiles.dependencies import DependencyBundle

    profile.accept(DependencyBundle(profile.identity, failed=True))
    port.finish(port.history[-1])
    assert len(port.history) == 2


def test_completed_dependencies_suppress_a_pending_loading_frame() -> None:
    from util import FakeIPC

    from saitenka.app.features.profiles.dependencies import DependencyBundle
    from saitenka.app.overlay_ids import OverlayId

    ipc = FakeIPC()
    port = FakeTimerPort()
    profile = _profile_session(ipc, port)
    profile.begin_loading()
    profile.publish(DependencyBundle(profile.identity))

    port.finish(port.history[-1])

    assert not any(command[:2] == ("overlay-add", OverlayId.LOADING) for command in ipc.commands)
    assert len(port.history) == 1


def test_negative_lifecycle_timer_delay_is_rejected() -> None:
    timers = LifecycleTimers(FakeTimerPort())
    try:
        timers.schedule(LifecycleTimerKind.TOAST_EXPIRY, -0.1, lambda: None)
    except ValueError as error:
        assert str(error) == "lifecycle timer delay must be non-negative"
    else:  # pragma: no cover - deadline contract
        raise AssertionError("negative lifecycle delay was accepted")


class BlockingTimerPort(FakeTimerPort):
    def __init__(self) -> None:
        super().__init__()
        self.schedule_entered = threading.Event()
        self.release_schedule = threading.Event()
        self.cancel_entered = threading.Event()
        self.release_cancel = threading.Event()
        self.block_schedule = True
        self.block_cancel = False

    def schedule_runtime_timer(self, **kwargs) -> bool:
        self.schedule_entered.set()
        if self.block_schedule:
            self.release_schedule.wait()
        return super().schedule_runtime_timer(**kwargs)

    def cancel_runtime_timer(self, timer: str) -> bool:
        self.cancel_entered.set()
        if self.block_cancel:
            self.release_cancel.wait()
        return super().cancel_runtime_timer(timer)


@pytest.mark.timeout(5)
def test_concurrent_schedule_cannot_admit_older_revision_after_newer() -> None:
    port = BlockingTimerPort()
    timers = LifecycleTimers(port)
    first = threading.Thread(
        target=lambda: timers.schedule(LifecycleTimerKind.TOAST_EXPIRY, 1.0, lambda: None)
    )
    second = threading.Thread(
        target=lambda: timers.schedule(LifecycleTimerKind.TOAST_EXPIRY, 2.0, lambda: None)
    )

    first.start()
    assert port.schedule_entered.wait(1)
    second.start()
    assert len(port.history) == 0
    port.block_schedule = False
    port.release_schedule.set()
    first.join(1)
    second.join(1)

    assert [item.identity.revision for item in port.history] == [1, 2]
    assert port.scheduled["lifecycle:toast-expiry"].identity.revision == 2


@pytest.mark.timeout(5)
def test_cancel_and_new_schedule_cannot_erase_new_revision() -> None:
    port = BlockingTimerPort()
    port.block_schedule = False
    port.release_schedule.set()
    timers = LifecycleTimers(port)
    timers.schedule(LifecycleTimerKind.LOADING_FRAME, 1.0, lambda: None)
    port.block_cancel = True
    cancel = threading.Thread(target=lambda: timers.cancel(LifecycleTimerKind.LOADING_FRAME))
    schedule = threading.Thread(
        target=lambda: timers.schedule(LifecycleTimerKind.LOADING_FRAME, 2.0, lambda: None)
    )

    cancel.start()
    assert port.cancel_entered.wait(1)
    schedule.start()
    assert len(port.history) == 1
    port.block_cancel = False
    port.release_cancel.set()
    cancel.join(1)
    schedule.join(1)

    assert [item.identity.revision for item in port.history] == [1, 3]
    assert port.scheduled["lifecycle:loading-frame"].identity.revision == 3


def test_close_rejects_new_timers_and_late_completion() -> None:
    port = FakeTimerPort()
    timers = LifecycleTimers(port)
    applied: list[bool] = []
    assert timers.schedule(
        LifecycleTimerKind.STARTUP_HEALTH,
        8.0,
        lambda: applied.append(True),
    )
    pending = port.history[-1]

    timers.close()

    assert not timers.schedule(LifecycleTimerKind.STARTUP_HEALTH, 1.0, lambda: applied.append(True))
    port.finish(pending)
    assert applied == []
