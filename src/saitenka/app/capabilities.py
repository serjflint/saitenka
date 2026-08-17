"""Session-owned, nonblocking capability snapshots for optional services."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    probe: Callable[[], bool]


def run_capability(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, CapabilityRequest):
        raise TypeError("invalid capability request")
    if cancelled.is_set():
        return False
    try:
        return bool(request.probe())
    except Exception:  # noqa: BLE001 -- optional capabilities fail closed
        return False


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


def configure_runtime_jobs(ipc) -> JobSubmitter | None:
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "capabilities",
        JobLanePolicy(capacity=4, workers=4),
        run_capability,
    ):
        return None
    return ipc.submit_runtime_job


class CapabilityProbe:
    """Publish a bounded probe result without making the event thread perform the probe."""

    def __init__(
        self,
        probe: Callable[[], bool],
        *,
        name: str,
        ttl: float,
        retry: float,
        timeout: float = 10.5,
        max_retry: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        submit: JobSubmitter | None = None,
    ) -> None:
        self._probe = probe
        self._name = name
        self._ttl = max(0.0, ttl)
        self._retry = max(0.0, retry)
        self._max_retry = max(self._retry, max_retry if max_retry is not None else self._retry * 8)
        self._timeout = max(0.001, timeout)
        self._clock = clock
        self._submit = submit
        self._lock = threading.Lock()
        self._generation = 0
        self._inflight = False
        self._closed = False
        self._next_due = 0.0
        self._started_at = 0.0
        self._failures = 0
        self._replacement_used = False
        self._value: bool | None = None

    @property
    def value(self) -> bool | None:
        with self._lock:
            return self._value

    def request(self, *, force: bool = False) -> bool:
        with self._lock:
            now = self._clock()
            if self._closed:
                return False
            if self._inflight:
                if now - self._started_at < self._timeout or self._replacement_used:
                    return False
                self._generation += 1
                self._inflight = False
                self._replacement_used = True
            if not force and now < self._next_due:
                return False
            self._inflight = True
            self._started_at = now
            generation = self._generation

        if self._submit is not None:
            return self._submit(
                owner=Owner.SESSION,
                identity=(self._name, generation),
                lane="capabilities",
                request=CapabilityRequest(self._probe),
                on_finished=self._finish_runtime,
            )

        def run() -> None:
            self._publish(
                generation,
                available=bool(run_capability(CapabilityRequest(self._probe), threading.Event())),
            )

        threading.Thread(target=run, name=f"saitenka-{self._name}-probe", daemon=True).start()
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._inflight = False

    def _finish_runtime(self, completion: EffectFinished) -> None:
        identity = completion.identity
        if not (
            isinstance(identity, tuple)
            and len(identity) == 2
            and identity[0] == self._name
            and isinstance(identity[1], int)
        ):
            return
        if completion.outcome is EffectOutcome.SUCCEEDED:
            self._publish(identity[1], available=bool(completion.result))
        else:
            self._schedule_retry(identity[1])

    def _schedule_retry(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self._closed:
                return
            self._inflight = False
            self._failures += 1
            delay = min(self._max_retry, self._retry * (2 ** (self._failures - 1)))
            self._next_due = self._clock() + delay

    def _publish(self, generation: int, *, available: bool) -> None:
        with self._lock:
            if generation != self._generation or self._closed:
                return
            self._inflight = False
            self._value = available
            if available:
                self._failures = 0
                delay = self._ttl
            else:
                self._failures += 1
                delay = min(self._max_retry, self._retry * (2 ** (self._failures - 1)))
            self._next_due = self._clock() + delay
