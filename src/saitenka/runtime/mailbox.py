"""Bounded wake-driven mailbox with protected lifecycle and completion lanes."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.runtime.effects import EffectId
from saitenka.runtime.events import (
    CloseRequested,
    CommandHandled,
    ConnectionLost,
    EffectFinished,
    EventEnvelope,
    EventOrigin,
    PropertyObserved,
    UserCommand,
)
from saitenka.runtime.limits import DEFAULT_RUNTIME_LIMITS

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.events import RuntimeEvent


#: Commands where adjacent repeats in one batch are one intent: a scroll accumulates, so two
#: notches mpv reported together are one movement. Nothing else is folded — a repeat of a command
#: that acts is the user asking for it again.
_COALESCING_COMMANDS = frozenset({"saitenka-scroll-up", "saitenka-scroll-down"})


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
    command_reserved: int
    close_requested: bool
    connection_lost: bool
    closed: bool


class SessionMailbox:
    """Three bounded lanes merged by envelope sequence at consumption time."""

    def __init__(
        self,
        *,
        normal_capacity: int = DEFAULT_RUNTIME_LIMITS.mailbox_normal,
        lifecycle_capacity: int = DEFAULT_RUNTIME_LIMITS.mailbox_lifecycle,
        terminal_capacity: int = DEFAULT_RUNTIME_LIMITS.mailbox_terminal,
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
        self._next_effect = 0
        self._terminal_reservations: set[EffectId] = set()
        self._terminal_enqueued: set[EffectId] = set()
        self._command_reservations: set[int] = set()
        self._close_latch: EventEnvelope | None = None
        self._connection_lost: EventEnvelope | None = None
        self._closed = False
        self._wakes = 0
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
        if isinstance(payload, CommandHandled) and payload.command_id is not None:
            raise TypeError("correlated command outcomes must use publish_command_terminal")
        with self._condition:
            if self._closed:
                raise MailboxFull("mailbox is closed")
            envelope = self._envelope_locked(payload, origin, connection_epoch)
            if isinstance(payload, CloseRequested):
                if self._close_latch is not None:
                    return self._close_latch
                self._close_latch = envelope
                self._condition.notify()
                return envelope
            if isinstance(payload, ConnectionLost):
                current = self._connection_lost
                current_payload = None if current is None else current.payload
                if (
                    not isinstance(current_payload, ConnectionLost)
                    or payload.connection_epoch > current_payload.connection_epoch
                ):
                    self._connection_lost = envelope
                    self._condition.notify()
                    return envelope
                assert current is not None
                return current
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
                command_reserved=len(self._command_reservations),
                close_requested=self._close_latch is not None,
                connection_lost=self._connection_lost is not None,
                closed=self._closed,
            )

    def publish_command(
        self,
        command: UserCommand,
        *,
        origin: EventOrigin,
        connection_epoch: int | None = None,
    ) -> EventEnvelope:
        command_id = command.command_id
        if command_id is None:
            raise ValueError("runtime commands require a command ID")
        with self._condition:
            if self._closed:
                raise MailboxFull("mailbox is closed")
            if command_id in self._command_reservations:
                raise ValueError(f"command already admitted: {command_id}")
            normal = self._queues[TrafficClass.NORMAL]
            used = len(normal) + len(self._command_reservations)
            if used + 2 > self._capacities[TrafficClass.NORMAL]:
                raise MailboxFull("normal mailbox lane cannot reserve a command outcome")
            self._command_reservations.add(command_id)
            envelope = self._envelope_locked(command, origin, connection_epoch)
            normal.append(envelope)
            self._condition.notify()
            return envelope

    def publish_command_terminal(
        self,
        outcome: CommandHandled,
        *,
        origin: EventOrigin,
        connection_epoch: int | None = None,
    ) -> bool:
        command_id = outcome.command_id
        if command_id is None:
            raise ValueError("runtime command outcomes require a command ID")
        with self._condition:
            if self._closed or command_id not in self._command_reservations:
                return False
            self._command_reservations.remove(command_id)
            envelope = self._envelope_locked(outcome, origin, connection_epoch)
            self._queues[TrafficClass.NORMAL].append(envelope)
            self._condition.notify()
            return True

    def allocate_effect(self) -> EffectId:
        """Take the next effect ID in this mailbox's terminal namespace.

        The mailbox allocates because the mailbox is what the ID has to be unique *within* — it is
        the key of `reserve_terminal`/`publish_terminal`/`retire_terminal`, and nothing else cares.
        A second allocator on one namespace has only `reserve_terminal` returning False as its
        collision detector, which reads identically to an overloaded lane.
        """
        return self.allocate_effects(1)[0]

    def allocate_effects(self, count: int) -> tuple[EffectId, ...]:
        """Take `count` IDs at once, for a caller that needs several before it can commit to any.

        Ascending matters, not adjacency — a command and its deadline timer correlate by the
        `effect-deadline:{target}` name, but `SessionReactor._apply` rejects an ID that does not
        exceed every ID it has already dispatched.
        """
        if count <= 0:
            raise ValueError("effect allocation count must be positive")
        with self._condition:
            first = self._next_effect
            self._next_effect += count
        return tuple(EffectId(value) for value in range(first, first + count))

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
            # A counter, not a flag: `wait_for` re-tests its predicate after every notify, so a
            # bare `notify_all` releases nobody. Comparing against the count read on entry also
            # makes a wake un-latched — it frees the receivers blocked *now* and leaves no state
            # for the next caller to trip over.
            woken = self._wakes
            self._condition.wait_for(
                lambda: self._has_events_locked() or self._closed or self._wakes != woken,
                timeout,
            )
            return self._pop_next_locked()

    def receive_ready(
        self,
        *,
        limit: int = DEFAULT_RUNTIME_LIMITS.mailbox_turn,
        start: EventEnvelope | None = None,
    ) -> tuple[EventEnvelope, ...]:
        if limit <= 0:
            raise ValueError("mailbox receive limit must be positive")
        items = [] if start is None else [start]
        with self._condition:
            while len(items) < limit and (envelope := self._pop_next_locked()):
                items.append(envelope)
        return self._coalesce(tuple(items))

    def drain_ready(self, *, start: EventEnvelope | None = None) -> tuple[EventEnvelope, ...]:
        return self.receive_ready(start=start)

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

    def wake(self) -> None:
        """Release every blocked `receive` without closing or publishing anything.

        A blocked receiver cannot observe a flag another thread set, and with no tick left the
        wait is bounded only by the earliest armed timer — so without this, "stop" would mean "stop
        after the next event, whenever that is". Also how a newly armed timer reaches a receiver
        already blocked under a later bound. `close` cannot serve either: it is terminal, and a stop
        has to be observable while the mailbox is still live enough to drain.
        """
        with self._condition:
            self._wakes += 1
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._terminal_reservations.clear()
            self._command_reservations.clear()
            self._condition.notify_all()

    def _publish_locked(self, envelope: EventEnvelope, traffic: TrafficClass) -> None:
        if self._closed:
            raise MailboxFull("mailbox is closed")
        queue = self._queues[traffic]
        reserved = len(self._command_reservations) if traffic == TrafficClass.NORMAL else 0
        if len(queue) + reserved >= self._capacities[traffic]:
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
        return (
            self._close_latch is not None
            or self._connection_lost is not None
            or any(self._queues.values())
        )

    def _pop_next_locked(self) -> EventEnvelope | None:
        if self._close_latch is not None:
            envelope = self._close_latch
            self._close_latch = None
            return envelope
        heads = [queue[0] for queue in self._queues.values() if queue]
        if self._connection_lost is not None:
            heads.append(self._connection_lost)
        if not heads:
            return None
        selected = min(heads, key=lambda envelope: envelope.sequence)
        if selected is self._connection_lost:
            self._connection_lost = None
            return selected
        for queue in self._queues.values():
            if queue and queue[0] is selected:
                return queue.popleft()
        raise AssertionError("selected mailbox event disappeared")

    @staticmethod
    def _coalesce(items: tuple[EventEnvelope, ...]) -> tuple[EventEnvelope, ...]:
        result: list[EventEnvelope] = []
        for envelope in items:
            if result and SessionMailbox._may_coalesce(result[-1], envelope):
                previous = result[-1]
                left, right = previous.payload, envelope.payload
                if isinstance(left, UserCommand) and isinstance(right, UserCommand):
                    assert left.command_id is not None
                    merged = replace(
                        right,
                        coalesced_ids=(*left.coalesced_ids, left.command_id, *right.coalesced_ids),
                    )
                    envelope = replace(envelope, payload=merged)
                result[-1] = envelope
            else:
                result.append(envelope)
        return tuple(result)

    @staticmethod
    def _may_coalesce(previous: EventEnvelope, current: EventEnvelope) -> bool:
        if previous.connection_epoch != current.connection_epoch:
            return False
        left, right = previous.payload, current.payload
        if isinstance(left, PropertyObserved) and isinstance(right, PropertyObserved):
            # The pointer only: every other observation is a fact a later one may depend on having
            # been seen, and mpv reports the cursor far faster than a turn can consume it.
            return left.name == right.name == "mouse-pos"
        if isinstance(left, UserCommand) and isinstance(right, UserCommand):
            return left.name == right.name and left.name in _COALESCING_COMMANDS
        return False
