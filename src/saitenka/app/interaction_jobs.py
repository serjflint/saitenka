"""Terminal telemetry for newest-wins tooltip and scroll intents."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from saitenka import otel_metrics


@dataclass(frozen=True, slots=True)
class _Active:
    job_id: int
    started: float


class InteractionJobs:
    def __init__(self) -> None:
        self._next = 0
        self._active: dict[str, _Active] = {}
        self._lock = threading.Lock()

    def begin(self, kind: str) -> int:
        self.finish(kind, "superseded")
        with self._lock:
            self._next += 1
            self._active[kind] = _Active(self._next, time.monotonic())
            return self._next

    def finish(self, kind: str, outcome: str, *, job_id: int | None = None) -> None:
        with self._lock:
            active = self._active.get(kind)
            if active is None or (job_id is not None and active.job_id != job_id):
                return
            self._active.pop(kind, None)
        latency_ms = (time.monotonic() - active.started) * 1_000.0
        with otel_metrics.traced(
            f"{kind}_request",
            job_id=str(active.job_id),
            outcome=outcome,
            latency_ms=f"{latency_ms:.3f}",
        ):
            pass

    def cancel_all(self) -> None:
        with self._lock:
            kinds = tuple(self._active)
        for kind in kinds:
            self.finish(kind, "cancelled")
