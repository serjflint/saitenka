"""Terminal telemetry for newest-wins tooltip and scroll intents."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from saitenka import otel_metrics


@dataclass(frozen=True, slots=True)
class _Active:
    job_id: int
    started: float
    span: otel_metrics.DeferredSpan
    terminal: Callable


class InteractionJobs:
    def __init__(self) -> None:
        self._next = 0
        self._active: dict[str, _Active] = {}
        self._lock = threading.Lock()

    def begin(self, kind: str) -> int:
        self.finish(kind, "superseded")
        with self._lock:
            self._next += 1

            def terminal(**attrs):
                with otel_metrics.traced(f"{kind}_request", **attrs):
                    pass

            self._active[kind] = _Active(
                self._next,
                time.monotonic(),
                otel_metrics.DeferredSpan(f"{kind}_lifetime", job_id=str(self._next)),
                otel_metrics.bind_context(terminal),
            )
            return self._next

    def finish(self, kind: str, outcome: str, *, job_id: int | None = None) -> None:
        with self._lock:
            active = self._active.get(kind)
            if active is None or (job_id is not None and active.job_id != job_id):
                return
            self._active.pop(kind, None)
        latency_ms = (time.monotonic() - active.started) * 1_000.0
        active.span.finish(
            outcome=outcome,
            latency_ms=round(latency_ms, 3),
        )
        active.terminal(job_id=str(active.job_id), outcome=outcome, latency_ms=round(latency_ms, 3))

    def cancel_all(self) -> None:
        with self._lock:
            kinds = tuple(self._active)
        for kind in kinds:
            self.finish(kind, "cancelled")
