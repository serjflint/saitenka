"""Unified shutdown: termination signals route to KeyboardInterrupt so cleanup runs."""

from __future__ import annotations

import signal

import pytest

from overlay.app import signals


def test_install_registers_termination_handlers(monkeypatch):
    registered = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: registered.__setitem__(sig, handler))
    signals.install()
    # SIGTERM exists on all platforms; its handler must be our KeyboardInterrupt raiser.
    assert signal.SIGTERM in registered
    with pytest.raises(KeyboardInterrupt):
        registered[signal.SIGTERM](signal.SIGTERM, None)


def test_install_is_noop_when_signal_unsupported(monkeypatch):
    def _boom(sig, handler):
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(signal, "signal", _boom)
    signals.install()  # must swallow the error, not raise
