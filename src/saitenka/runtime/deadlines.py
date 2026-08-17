"""Parent/child deadline bookkeeping independent of adapters and clocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime.effects import (
    CancelEffect,
    EffectDeadline,
    EffectId,
    EffectOutcome,
    ExpireEffect,
    ScheduleTimer,
)

if TYPE_CHECKING:
    from saitenka.runtime.events import EffectFinished


@dataclass(frozen=True, slots=True)
class DeadlinePair:
    target_effect_id: EffectId
    timer_effect_id: EffectId


class DeadlineRegistry:
    """Relate an adapter effect to its independently terminal timer child."""

    def __init__(self) -> None:
        self._by_target: dict[EffectId, ScheduleTimer] = {}
        self._target_by_timer: dict[EffectId, EffectId] = {}

    def register(self, target_effect_id: EffectId, timer: ScheduleTimer) -> DeadlinePair:
        if target_effect_id == timer.effect_id:
            raise ValueError("deadline timer must have a distinct effect ID")
        if target_effect_id in self._by_target or timer.effect_id in self._target_by_timer:
            raise ValueError("deadline pair already registered")
        if timer.identity != EffectDeadline(target_effect_id, timer.due_at):
            raise ValueError("timer identity does not match its target deadline")
        self._by_target[target_effect_id] = timer
        self._target_by_timer[timer.effect_id] = target_effect_id
        return DeadlinePair(target_effect_id, timer.effect_id)

    def target_finished(self, completion: EffectFinished) -> CancelEffect | None:
        timer = self._by_target.pop(completion.effect_id, None)
        if timer is None:
            return None
        del self._target_by_timer[timer.effect_id]
        return CancelEffect(timer.effect_id, timer.owner, timer.identity)

    def timer_finished(self, completion: EffectFinished) -> ExpireEffect | None:
        target_id = self._target_by_timer.pop(completion.effect_id, None)
        if target_id is None:
            return None
        timer = self._by_target.pop(target_id)
        if completion.outcome != EffectOutcome.SUCCEEDED:
            return None
        return ExpireEffect(target_id, timer.due_at)

    def retire(self, effect_id: EffectId) -> None:
        timer = self._by_target.pop(effect_id, None)
        if timer is not None:
            self._target_by_timer.pop(timer.effect_id, None)
            return
        target = self._target_by_timer.pop(effect_id, None)
        if target is not None:
            self._by_target.pop(target, None)
