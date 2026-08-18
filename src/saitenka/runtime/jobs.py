"""Bounded background-job lanes that publish terminal runtime outcomes."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime.effects import EffectError, EffectId, EffectOutcome, SubmitJob
from saitenka.runtime.events import EffectFinished, EventOrigin

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.runtime.mailbox import SessionMailbox


@dataclass(frozen=True, slots=True)
class JobLanePolicy:
    capacity: int
    workers: int = 1

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("job lane capacity must be positive")
        if self.workers < 1:
            raise ValueError("job lane worker count must be positive")


class JobHandler(Protocol):
    def __call__(self, request: object, cancelled: threading.Event) -> object: ...


@dataclass(slots=True)
class _Accepted:
    effect: SubmitJob
    cancelled: threading.Event


class _Lane:
    def __init__(
        self,
        name: str,
        policy: JobLanePolicy,
        handler: JobHandler,
        complete: Callable[[EffectFinished], None],
    ) -> None:
        self.name = name
        self.policy = policy
        self.handler = handler
        self.complete = complete
        self.condition = threading.Condition()
        self.queue: deque[_Accepted] = deque()
        self.pending: dict[int, _Accepted] = {}
        self.closed = False
        self.threads = tuple(
            threading.Thread(
                target=self._run,
                name=f"saitenka-job-{name}-{index}",
                daemon=True,
            )
            for index in range(policy.workers)
        )
        for thread in self.threads:
            thread.start()

    def admit(self, effect: SubmitJob) -> bool:
        accepted = _Accepted(effect, threading.Event())
        with self.condition:
            if self.closed or len(self.pending) >= self.policy.capacity:
                return False
            self.pending[effect.effect_id.value] = accepted
            self.queue.append(accepted)
            self.condition.notify()
        return True

    def close(self, deadline: float) -> None:
        with self.condition:
            if self.closed:
                return
            self.closed = True
            accepted = tuple(self.pending.values())
            self.pending.clear()
            self.queue.clear()
            for job in accepted:
                job.cancelled.set()
            self.condition.notify_all()
        for job in accepted:
            self.complete(
                EffectFinished(
                    job.effect.effect_id,
                    job.effect.owner,
                    job.effect.identity,
                    EffectOutcome.CANCELLED,
                )
            )
        for thread in self.threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def _run(self) -> None:
        while True:
            with self.condition:
                while not self.queue and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                accepted = self.queue.popleft()
            effect = accepted.effect
            try:
                result = self.handler(effect.request, accepted.cancelled)
                outcome = (
                    EffectOutcome.CANCELLED
                    if accepted.cancelled.is_set()
                    else EffectOutcome.SUCCEEDED
                )
                error = None
            except Exception:  # noqa: BLE001 -- adapter failures become bounded outcomes
                result = None
                outcome = EffectOutcome.FAILED
                error = EffectError.INTERNAL
            with self.condition:
                current = self.pending.pop(effect.effect_id.value, None)
            if current is None:
                continue
            self.complete(
                EffectFinished(
                    effect.effect_id,
                    effect.owner,
                    effect.identity,
                    outcome,
                    result=result,
                    error=error,
                )
            )


class LocalJobLane:
    """A lane whose terminals go straight back to the submitting feature, with no mailbox.

    Admission, execution and cancellation are the broker's — only the terminal's destination
    differs. It exists so a feature keeps ONE execution path whether or not the runtime gateway is
    installed: a feature that runs a private thread for the un-gatewayed case has two
    implementations of the same lane, and the untested one is the one that drifts.
    """

    def __init__(self, name: str, policy: JobLanePolicy, handler: JobHandler) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._callbacks: dict[EffectId, Callable[[EffectFinished], None]] = {}
        self._issued = 0
        self._lane = _Lane(name, policy, handler, self._complete)

    def submit(
        self,
        *,
        owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool:
        if lane != self._name:
            return False
        with self._lock:
            self._issued += 1
            effect_id = EffectId(self._issued)
            self._callbacks[effect_id] = on_finished
        if self._lane.admit(SubmitJob(effect_id, owner, identity, lane, request)):
            return True
        with self._lock:
            self._callbacks.pop(effect_id, None)
        return False

    def close(self, timeout: float = 2.0) -> None:
        self._lane.close(time.monotonic() + max(0.0, timeout))

    def _complete(self, completion: EffectFinished) -> None:
        with self._lock:
            callback = self._callbacks.pop(completion.effect_id, None)
        if callback is not None:
            callback(completion)


class JobBroker:
    """Own lane admission and worker lifetime; never owns feature state."""

    def __init__(self, mailbox: SessionMailbox) -> None:
        self._mailbox = mailbox
        self._lock = threading.Lock()
        self._lanes: dict[str, _Lane] = {}
        self._closed = False

    def register(self, name: str, policy: JobLanePolicy, handler: JobHandler) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job broker is closed")
            if name in self._lanes:
                raise ValueError(f"job lane already registered: {name}")
            self._lanes[name] = _Lane(name, policy, handler, self._complete)

    def dispatch(self, effect: SubmitJob) -> bool:
        with self._lock:
            lane = self._lanes.get(effect.lane)
            if self._closed or lane is None:
                return False
        return lane.admit(effect)

    def close(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            if self._closed:
                return
            self._closed = True
            lanes = tuple(self._lanes.values())
        for lane in lanes:
            lane.close(deadline)

    def close_lane(self, name: str, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            lane = self._lanes.pop(name, None)
        if lane is None:
            return False
        lane.close(deadline)
        return True

    def _complete(self, completion: EffectFinished) -> None:
        self._mailbox.publish_terminal(
            completion,
            origin=EventOrigin.WORKER,
            connection_epoch=None,
        )
