"""Raw cue-transition checkpoints captured with the session's telemetry."""

from __future__ import annotations

import time
from contextlib import contextmanager
from itertools import pairwise
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.app import telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@contextmanager
def transition_probe() -> Iterator[Callable[[str], None]]:
    if not telemetry.span_gate:
        yield _ignore
        return
    wall_start = time.time_ns()
    marks = [("start", time.monotonic_ns(), time.thread_time_ns())]

    def mark(name: str) -> None:
        marks.append((name, time.monotonic_ns(), time.thread_time_ns()))

    try:
        yield mark
    finally:
        # Export after measuring: span entry/exit overhead belongs to the operation measured.
        with otel_metrics.traced("cue_transition_probe") as span:
            span.set("checkpoint_wall_start_ns", str(wall_start))
            for previous, current in pairwise(marks):
                name, wall, cpu = current
                span.set(f"{name}.wall_ms", (wall - previous[1]) / 1_000_000)
                span.set(f"{name}.cpu_ms", (cpu - previous[2]) / 1_000_000)
                span.set(f"{name}.offset_ms", (wall - marks[0][1]) / 1_000_000)


def _ignore(_name: str) -> None:
    pass
