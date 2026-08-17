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


class LegacyRuntimeBridge:
    """Drive typed command/deadline effects from the legacy Reader turn."""

    def __init__(
        self,
        mailbox: SessionMailbox,
        command_adapter: CommandAdapter,
        router: TerminalRouter,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mailbox = mailbox
        self._command_adapter = command_adapter
        self._clock = clock
        self._lock = threading.Lock()
        self._next_effect = 0
        self._callbacks: dict[EffectId, Callable[[EffectFinished], None]] = {}
        self._deadlines = DeadlineRegistry()
        self._timers = TimerScheduler()
        router.install_runtime_bridge(self)

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
        with self._lock:
            target = EffectId(self._next_effect)
            timer = EffectId(self._next_effect + 1)
            self._next_effect += 2
        return target, timer

    def _reserve_pair(self, target: EffectId, timer: EffectId) -> bool:
        if not self._mailbox.reserve_terminal(target):
            return False
        if self._mailbox.reserve_terminal(timer):
            return True
        self._mailbox.cancel_reservation(target)
        return False
