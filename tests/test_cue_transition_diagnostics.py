"""Calibration and telemetry gating for raw cue-transition checkpoints."""

from contextlib import contextmanager

import pytest

from saitenka.app import cue_transition_diagnostics as diagnostics


def test_probe_separates_elapsed_wait_from_thread_cpu(monkeypatch):
    gate = diagnostics.telemetry.ActiveGate()
    gate.set(value=True)
    monkeypatch.setattr(diagnostics.telemetry, "span_gate", gate)
    wall = iter((1_000_000_000, 1_020_000_000, 1_023_000_000))
    cpu = iter((100_000_000, 101_000_000, 103_000_000))
    monkeypatch.setattr(diagnostics.time, "monotonic_ns", lambda: next(wall))
    monkeypatch.setattr(diagnostics.time, "thread_time_ns", lambda: next(cpu))
    monkeypatch.setattr(diagnostics.time, "time_ns", lambda: 7_000_000_000)
    attributes = {}

    class Span:
        def set(self, key, value):
            attributes[key] = value

    @contextmanager
    def export(_name):
        # No time reads remain: diagnostic export is outside the measured intervals.
        assert next(wall, None) is None
        assert next(cpu, None) is None
        yield Span()

    monkeypatch.setattr(diagnostics.otel_metrics, "traced", export)

    with diagnostics.transition_probe() as mark:
        mark("wait")
        mark("work")

    assert attributes == {
        "checkpoint_wall_start_ns": "7000000000",
        "wait.wall_ms": 20.0,
        "wait.cpu_ms": 1.0,
        "wait.offset_ms": 20.0,
        "work.wall_ms": 3.0,
        "work.cpu_ms": 2.0,
        "work.offset_ms": 23.0,
    }


def test_disabled_probe_does_not_read_clocks_or_export(monkeypatch):
    monkeypatch.setattr(diagnostics.telemetry, "span_gate", diagnostics.telemetry.ActiveGate())

    def unavailable(*_args):
        pytest.fail("disabled diagnostics must not collect or export timings")

    monkeypatch.setattr(diagnostics.time, "time_ns", unavailable)
    monkeypatch.setattr(diagnostics.time, "monotonic_ns", unavailable)
    monkeypatch.setattr(diagnostics.time, "thread_time_ns", unavailable)
    monkeypatch.setattr(diagnostics.otel_metrics, "traced", unavailable)

    with diagnostics.transition_probe() as mark:
        mark("operation")
