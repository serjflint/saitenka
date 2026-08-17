"""Bounded wake-driven mailbox with protected lifecycle and completion lanes."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.runtime.events import EffectFinished, EventEnvelope, EventOrigin

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.effects import EffectId
    from saitenka.runtime.events import RuntimeEvent


class TrafficClass(StrEnum):
    NORMAL = "normal"
    LIFECYCLE = "lifecycle"
    TERMINAL = "terminal"


class MailboxFull(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MailboxSnapshot:
    normal: int
    lifecycle: int
    terminal: int
    terminal_reserved: int
    terminal_enqueued: int
    closed: bool


class SessionMailbox:
    """Three bounded lanes merged by envelope sequence at consumption time."""

    def __init__(
        self,
        *,
        normal_capacity: int = 256,
        lifecycle_capacity: int = 8,
        terminal_capacity: int = 64,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(normal_capacity, lifecycle_capacity, terminal_capacity) <= 0:
            raise ValueError("mailbox capacities must be positive")
        self._capacities = {
            TrafficClass.NORMAL: normal_capacity,
            TrafficClass.LIFECYCLE: lifecycle_capacity,
            TrafficClass.TERMINAL: terminal_capacity,
        }
        self._queues: dict[TrafficClass, deque[EventEnvelope]] = {
            traffic: deque() for traffic in TrafficClass
        }
        self._clock = clock
        self._next_sequence = 0
        self._terminal_reservations: set[EffectId] = set()
        self._terminal_enqueued: set[EffectId] = set()
        self._closed = False
        self._condition = threading.Condition()

    def publish(
        self,
        payload: RuntimeEvent,
        *,
        origin: EventOrigin,
        traffic: TrafficClass,
        connection_epoch: int | None = None,
    ) -> EventEnvelope:
        if traffic == TrafficClass.TERMINAL:
            raise ValueError("terminal events must use publish_terminal")
        if isinstance(payload, EffectFinished):
            raise TypeError("effect completions must use publish_terminal")
        with self._condition:
            envelope = self._envelope_locked(payload, origin, connection_epoch)
            self._publish_locked(envelope, traffic)
            return envelope

    @property
    def snapshot(self) -> MailboxSnapshot:
        with self._condition:
            return MailboxSnapshot(
                normal=len(self._queues[TrafficClass.NORMAL]),
                lifecycle=len(self._queues[TrafficClass.LIFECYCLE]),
                terminal=len(self._queues[TrafficClass.TERMINAL]),
                terminal_reserved=len(self._terminal_reservations),
                terminal_enqueued=len(self._terminal_enqueued),
                closed=self._closed,
            )

    def reserve_terminal(self, effect_id: EffectId) -> bool:
        with self._condition:
            if (
                self._closed
                or effect_id in self._terminal_reservations
                or effect_id in self._terminal_enqueued
            ):
                return False
            used = len(self._queues[TrafficClass.TERMINAL]) + len(self._terminal_reservations)
            if used >= self._capacities[TrafficClass.TERMINAL]:
                return False
            self._terminal_reservations.add(effect_id)
            return True

    def publish_terminal(
        self,
        completion: EffectFinished,
        *,
        origin: EventOrigin,
        connection_epoch: int | None = None,
    ) -> bool:
        with self._condition:
            if self._closed:
                return False
            effect_id = completion.effect_id
            if effect_id not in self._terminal_reservations:
                return False
            self._terminal_reservations.remove(effect_id)
            self._terminal_enqueued.add(effect_id)
            envelope = self._envelope_locked(completion, origin, connection_epoch)
            self._queues[TrafficClass.TERMINAL].append(envelope)
            self._condition.notify()
            return True

    def receive(self, *, timeout: float | None = None) -> EventEnvelope | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._has_events_locked() or self._closed,
                timeout,
            )
            return self._pop_next_locked()

    def drain_ready(self, *, start: EventEnvelope | None = None) -> tuple[EventEnvelope, ...]:
        items = [] if start is None else [start]
        with self._condition:
            while envelope := self._pop_next_locked():
                items.append(envelope)
        return tuple(items)

    def retire_terminal(self, effect_id: EffectId) -> bool:
        with self._condition:
            if effect_id not in self._terminal_enqueued:
                return False
            self._terminal_enqueued.remove(effect_id)
            return True

    def terminal_enqueued(self, effect_id: EffectId) -> bool:
        with self._condition:
            return effect_id in self._terminal_enqueued

    def cancel_reservation(self, effect_id: EffectId) -> bool:
        with self._condition:
            if effect_id not in self._terminal_reservations:
                return False
            self._terminal_reservations.remove(effect_id)
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._terminal_reservations.clear()
            self._condition.notify_all()

    def _publish_locked(self, envelope: EventEnvelope, traffic: TrafficClass) -> None:
        if self._closed:
            raise MailboxFull("mailbox is closed")
        queue = self._queues[traffic]
        if len(queue) >= self._capacities[traffic]:
            raise MailboxFull(f"{traffic.value} mailbox lane is full")
        queue.append(envelope)
        self._condition.notify()

    def _envelope_locked(
        self,
        payload: RuntimeEvent,
        origin: EventOrigin,
        connection_epoch: int | None,
    ) -> EventEnvelope:
        sequence = self._next_sequence
        self._next_sequence += 1
        return EventEnvelope(sequence, self._clock(), origin, connection_epoch, payload)

    def _has_events_locked(self) -> bool:
        return any(self._queues.values())

    def _pop_next_locked(self) -> EventEnvelope | None:
        heads = [queue[0] for queue in self._queues.values() if queue]
        if not heads:
            return None
        selected = min(heads, key=lambda envelope: envelope.sequence)
        for queue in self._queues.values():
            if queue and queue[0] is selected:
                return queue.popleft()
        raise AssertionError("selected mailbox event disappeared")
