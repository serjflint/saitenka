"""Tests for overlay.app.telemetry: opt-in lifecycle, no-op-when-disabled, idempotence."""

from __future__ import annotations

import sys

import pytest

from overlay.app import telemetry
from overlay.app.config import TelemetryOptions


@pytest.fixture(autouse=True)
def _reset_providers():
    """configure()/shutdown() mutate module globals; isolate each test."""
    telemetry.shutdown()
    yield
    telemetry.shutdown()


def test_disabled_is_a_full_noop(tmp_path):
    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=False, export_dir=str(export)))
    assert telemetry.is_enabled() is False
    assert not export.exists()  # no directory created, no providers stood up


def test_enabled_stands_up_providers_and_creates_export_dir(tmp_path):
    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    assert telemetry.is_enabled() is True
    assert export.is_dir()


def test_configure_is_idempotent(tmp_path):
    export = tmp_path / "telemetry"
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    from opentelemetry import trace

    first = trace.get_tracer_provider()
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(export)))
    assert trace.get_tracer_provider() is first


def test_shutdown_resets_state(tmp_path):
    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    assert telemetry.is_enabled() is True
    telemetry.shutdown()
    assert telemetry.is_enabled() is False
    telemetry.shutdown()  # a second call is a harmless no-op


def test_export_dir_defaults_to_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "cache_dir", lambda: tmp_path)
    assert telemetry.export_dir(TelemetryOptions()) == tmp_path / "telemetry"


def test_missing_extra_stays_disabled(monkeypatch, tmp_path, caplog):
    """Simulate the observability extra not being installed: opentelemetry imports fail inside
    configure(), and telemetry must stay fully off rather than raise."""
    real_import = __import__

    def _fake_import(name, *a, **kw):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr("builtins.__import__", _fake_import)

    telemetry.configure(TelemetryOptions(enabled=True, export_dir=str(tmp_path / "t")))
    assert telemetry.is_enabled() is False


def test_active_gate_defaults_off_and_toggles():
    gate = telemetry.ActiveGate()
    assert bool(gate) is False
    gate.set(True)
    assert bool(gate) is True
    gate.set(False)
    assert bool(gate) is False
