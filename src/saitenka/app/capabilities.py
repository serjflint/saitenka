"""Session-owned, nonblocking capability snapshots for optional services."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    generation: int
    available: bool


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
    ) -> None:
        self._probe = probe
        self._name = name
        self._ttl = max(0.0, ttl)
        self._retry = max(0.0, retry)
        self._max_retry = max(self._retry, max_retry if max_retry is not None else self._retry * 8)
        self._timeout = max(0.001, timeout)
        self._clock = clock
        self._lock = threading.Lock()
        self._results: queue.SimpleQueue[CapabilityResult] = queue.SimpleQueue()
        self._generation = 0
        self._inflight = False
        self._closed = False
        self._next_due = 0.0
        self._started_at = 0.0
        self._failures = 0
        self._replacement_used = False
        self.value: bool | None = None

    def request(self, *, force: bool = False) -> bool:
        with self._lock:
            now = self._clock()
            if self._closed:
                return False
            if self._inflight:
                if now - self._started_at < self._timeout or self._replacement_used:
                    return False
                # The old daemon is quarantined by generation. It may finish, but cannot publish.
                self._generation += 1
                self._inflight = False
                self._replacement_used = True
            if not force and now < self._next_due:
                return False
            self._inflight = True
            self._started_at = now
            generation = self._generation

        def run() -> None:
            try:
                available = bool(self._probe())
            except Exception:  # noqa: BLE001 -- an optional capability fails closed
                available = False
            self._results.put(CapabilityResult(generation, available))

        threading.Thread(
            target=run,
            name=f"saitenka-{self._name}-probe",
            daemon=True,
        ).start()
        return True

    def apply(self) -> bool:
        """Apply completed results; return whether the published value changed."""
        changed = False
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                if result.generation != self._generation or self._closed:
                    continue
                self._inflight = False
                old = self.value
                self.value = result.available
                if result.available:
                    self._failures = 0
                    delay = self._ttl
                else:
                    self._failures += 1
                    delay = min(self._max_retry, self._retry * (2 ** (self._failures - 1)))
                self._next_due = self._clock() + delay
                changed = changed or old != self.value
        return changed

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._inflight = False
