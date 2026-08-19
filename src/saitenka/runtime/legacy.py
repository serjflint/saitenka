"""Temporary Reader-thread driver for typed effects during runtime migration."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.deadlines import DeadlineRegistry
from saitenka.runtime.effects import (
    EffectDeadline,
    EffectError,
    EffectId,
    EffectOutcome,
    Owner,
    ScheduleTimer,
    SendMpvCommand,
    SubmitJob,
)
from saitenka.runtime.events import EffectFinished, EventOrigin
from saitenka.runtime.timers import TimerScheduler

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.mailbox import SessionMailbox


class CommandAdapter(Protocol):
    @property
    def connection_epoch(self) -> int: ...

    def dispatch(self, effect: SendMpvCommand) -> bool: ...

    def expire(self, control) -> None: ...


class TerminalRouter(Protocol):
    def install_runtime_bridge(self, bridge: LegacyRuntimeBridge) -> None: ...


class JobAdapter(Protocol):
    def dispatch(self, effect: SubmitJob) -> bool: ...


class LegacyRuntimeBridge:
    """Drive typed command/deadline effects from the legacy Reader turn."""

    def __init__(
        self,
        mailbox: SessionMailbox,
        command_adapter: CommandAdapter,
        router: TerminalRouter,
        *,
        job_adapter: JobAdapter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mailbox = mailbox
        self._command_adapter = command_adapter
        self._job_adapter = job_adapter
        self._clock = clock
        self._lock = threading.Lock()
        self._callbacks: dict[EffectId, Callable[[EffectFinished], None]] = {}
        self._timer_callbacks: dict[EffectId, Callable[[EffectFinished], None]] = {}
        self._job_callbacks: dict[EffectId, Callable[[EffectFinished], None]] = {}
        self._deadlines = DeadlineRegistry()
        self._timers = TimerScheduler()
        router.install_runtime_bridge(self)

    def submit_job(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool:
        effect_id = self._allocate_one()
        effect = SubmitJob(effect_id, owner, identity, lane, request)
        if self._job_adapter is None or not self._mailbox.reserve_terminal(effect_id):
            on_finished(
                EffectFinished(
                    effect_id,
                    owner,
                    identity,
                    EffectOutcome.REJECTED,
                    error=EffectError.OVERLOADED,
                )
            )
            return False
        with self._lock:
            self._job_callbacks[effect_id] = on_finished
        if self._job_adapter.dispatch(effect):
            return True
        with self._lock:
            self._job_callbacks.pop(effect_id, None)
        self._mailbox.cancel_reservation(effect_id)
        on_finished(
            EffectFinished(
                effect_id,
                owner,
                identity,
                EffectOutcome.REJECTED,
                error=EffectError.OVERLOADED,
            )
        )
        return False

    @property
    def connection_epoch(self) -> int:
        return self._command_adapter.connection_epoch

    def submit_mpv(
        self,
        *,
        owner: Owner,
        identity: object,
        command: tuple[object, ...],
        timeout_s: float,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool:
        now = self._clock()
        target_id, timer_id = self._allocate_pair()
        deadline = now + timeout_s
        target = SendMpvCommand(
            target_id,
            owner,
            identity,
            command,
            deadline,
            self.connection_epoch,
        )
        timer_identity = EffectDeadline(target_id, deadline)
        timer = ScheduleTimer(
            timer_id,
            owner,
            timer_identity,
            f"effect-deadline:{target_id.value}",
            deadline,
            target.connection_epoch,
        )
        if not self._reserve_pair(target_id, timer_id):
            on_finished(
                EffectFinished(
                    target_id,
                    owner,
                    identity,
                    EffectOutcome.REJECTED,
                    error=EffectError.OVERLOADED,
                )
            )
            return False
        with self._lock:
            self._callbacks[target_id] = on_finished
            self._deadlines.register(target_id, timer)
            self._timers.schedule(timer)
        if self._command_adapter.dispatch(target):
            return True
        self._mailbox.publish_terminal(
            EffectFinished(
                target.effect_id,
                target.owner,
                target.identity,
                EffectOutcome.REJECTED,
                error=EffectError.DISCONNECTED,
            ),
            origin=EventOrigin.MPV,
            connection_epoch=target.connection_epoch,
        )
        return True

    def schedule_timer(
        self,
        *,
        owner: Owner,
        identity: object,
        timer: str,
        due_at: float,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool:
        effect_id = self._allocate_one()
        effect = ScheduleTimer(
            effect_id,
            owner,
            identity,
            timer,
            due_at,
            self.connection_epoch,
        )
        if not self._mailbox.reserve_terminal(effect_id):
            on_finished(
                EffectFinished(
                    effect_id,
                    owner,
                    identity,
                    EffectOutcome.REJECTED,
                    error=EffectError.OVERLOADED,
                )
            )
            return False
        with self._lock:
            self._timer_callbacks[effect_id] = on_finished
            replaced = self._timers.schedule(effect)
        if replaced is not None:
            self._mailbox.publish_terminal(
                replaced,
                origin=EventOrigin.TIMER,
                connection_epoch=None,
            )
        return True

    def cancel_timer(self, timer: str) -> bool:
        with self._lock:
            cancelled = self._timers.cancel(timer)
        if cancelled is None:
            return False
        self._mailbox.publish_terminal(
            cancelled,
            origin=EventOrigin.TIMER,
            connection_epoch=None,
        )
        return True

    def publish_due(self) -> None:
        with self._lock:
            due = self._timers.pop_due(self._clock())
        for completion in due:
            self._mailbox.publish_terminal(
                completion,
                origin=EventOrigin.TIMER,
                connection_epoch=None,
            )

    def handle_terminal(self, completion: EffectFinished) -> None:
        if not self._mailbox.retire_terminal(completion.effect_id):
            return
        with self._lock:
            timer_callback = self._timer_callbacks.pop(completion.effect_id, None)
        if timer_callback is not None:
            timer_callback(completion)
            return
        with self._lock:
            job_callback = self._job_callbacks.pop(completion.effect_id, None)
        if job_callback is not None:
            job_callback(completion)
            return
        expire = None
        with self._lock:
            callback = self._callbacks.pop(completion.effect_id, None)
            if callback is not None:
                cancel = self._deadlines.target_finished(completion)
                cancelled = self._timers.cancel(f"effect-deadline:{completion.effect_id.value}")
            else:
                cancel = None
                cancelled = None
                expire = self._deadlines.timer_finished(completion)
        if callback is not None:
            if cancel is not None and cancelled is not None:
                self._mailbox.publish_terminal(
                    cancelled,
                    origin=EventOrigin.TIMER,
                    connection_epoch=None,
                )
            callback(completion)
            return
        if expire is not None:
            self._command_adapter.expire(expire)

    def _allocate_pair(self) -> tuple[EffectId, EffectId]:
        target, timer = self._mailbox.allocate_effects(2)
        return target, timer

    def _allocate_one(self) -> EffectId:
        return self._mailbox.allocate_effect()

    def _reserve_pair(self, target: EffectId, timer: EffectId) -> bool:
        if not self._mailbox.reserve_terminal(target):
            return False
        if self._mailbox.reserve_terminal(timer):
            return True
        self._mailbox.cancel_reservation(target)
        return False
