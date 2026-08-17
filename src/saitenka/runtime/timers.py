"""Deterministic named timer adapter; production wakeup wiring comes with the driver switch."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from saitenka.runtime.effects import EffectOutcome, ScheduleTimer
from saitenka.runtime.events import EffectFinished


@dataclass(order=True, slots=True)
class _Entry:
    due_at: float
    order: int
    effect: ScheduleTimer
    cancelled: bool = False


class TimerScheduler:
    def __init__(self) -> None:
        self._heap: list[_Entry] = []
        self._by_timer: dict[str, _Entry] = {}
        self._next_order = 0

    @property
    def next_deadline(self) -> float | None:
        self._discard_cancelled()
        return self._heap[0].due_at if self._heap else None

    def schedule(self, effect: ScheduleTimer) -> EffectFinished | None:
        replaced = self.cancel(effect.timer)
        entry = _Entry(effect.due_at, self._next_order, effect)
        self._next_order += 1
        self._by_timer[effect.timer] = entry
        heapq.heappush(self._heap, entry)
        return replaced

    def cancel(self, timer: str) -> EffectFinished | None:
        entry = self._by_timer.pop(timer, None)
        if entry is None:
            return None
        entry.cancelled = True
        return self._finish(entry.effect, EffectOutcome.CANCELLED)

    def pop_due(self, now: float) -> tuple[EffectFinished, ...]:
        if not math.isfinite(now) or now < 0:
            raise ValueError("timer clock must be finite and non-negative")
        due: list[EffectFinished] = []
        self._discard_cancelled()
        while self._heap and self._heap[0].due_at <= now:
            entry = heapq.heappop(self._heap)
            if entry.cancelled:
                continue
            if self._by_timer.get(entry.effect.timer) is not entry:
                continue
            del self._by_timer[entry.effect.timer]
            due.append(self._finish(entry.effect, EffectOutcome.SUCCEEDED))
        return tuple(due)

    def _discard_cancelled(self) -> None:
        while self._heap and self._heap[0].cancelled:
            heapq.heappop(self._heap)

    @staticmethod
    def _finish(effect: ScheduleTimer, outcome: EffectOutcome) -> EffectFinished:
        return EffectFinished(
            effect.effect_id, effect.owner, effect.identity, outcome, effect.timer
        )
