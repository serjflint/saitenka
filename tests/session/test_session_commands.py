"""The owner-thread command seam and its transport admission policy."""

from __future__ import annotations

import pytest
from session_builder import build_session
from util import FakeIPC, bare_gateway

from saitenka.app.config import ReaderOptions
from saitenka.app.runtime import merge_command_handlers
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.session.routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.events import ConnectionLost


def test_command_families_cannot_replace_each_other_at_the_shell() -> None:
    with pytest.raises(ValueError, match="already registered"):
        merge_command_handlers({"same": lambda: None}, {"same": lambda: None})


@pytest.mark.timeout(5)
def test_a_command_reaches_its_owner_thread_handler_once(monkeypatch) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.start()
    handled: list[str] = []
    monkeypatch.setattr(
        reader.graph.commands, "handle", lambda command: handled.append(command.name)
    )
    try:
        ipc.emit({"event": "client-message", "args": ["saitenka-help"]})
        reader.pump()
    finally:
        reader.close()
        gateway.close()

    assert handled == ["saitenka-help"]


@pytest.mark.parametrize("transport_lost", [False, True])
@pytest.mark.timeout(5)
def test_the_transport_decides_whether_a_command_runs(monkeypatch, transport_lost) -> None:
    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = build_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.start()
    handled: list[str] = []
    monkeypatch.setattr(
        reader.graph.commands, "handle", lambda command: handled.append(command.name)
    )
    try:
        if transport_lost:
            gateway.publish_session_event(ConnectionLost(0))
        ipc.emit({"event": "client-message", "args": ["saitenka-help"]})
        reader.pump()
        outcomes = gateway.snapshot.command_outcomes
    finally:
        reader.close()
        gateway.close()

    assert handled == ([] if transport_lost else ["saitenka-help"])
    assert outcomes == (1 if transport_lost else 0)
