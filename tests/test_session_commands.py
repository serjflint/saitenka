"""`Owner.SESSION`'s command intake: what the reducer decides, and who runs the command.

The act is the one thing that did *not* move — `commands.dispatch` and its binding table are where
they were. What moved is the decision to consult them, so every oracle here is about arrival: that
the command runs exactly once, that a refusal is still the performer's call, and that the batch's
coalescing window survives the trip through the reactor.
"""

from __future__ import annotations

import pytest
from util import FakeIPC, runtime_gateway

from saitenka.app.controller import Reader
from saitenka.app.session_routes import install_session_reactor
from saitenka.app.subtitle_render import NullRenderer
from saitenka.runtime.effects import RunUserCommand
from saitenka.runtime.events import ConnectionLost, SessionStarting, UserCommand
from saitenka.runtime.user_command import CommandIntake, reduce_user_command

HELP = UserCommand("saitenka-help", command_id=3)


def test_a_command_rides_out_as_an_effect_carrying_itself() -> None:
    """An effect with a subject: two commands in one batch are the same act with different
    arguments, so the reducer cannot leave the argument behind."""
    result = reduce_user_command(CommandIntake(), HELP)

    assert result.effects == (RunUserCommand(HELP),)
    assert result.state == CommandIntake()


def test_an_unrelated_session_event_decides_no_command() -> None:
    """The slot is a slice, so every SESSION feature sees every SESSION event by broadcast."""
    assert reduce_user_command(CommandIntake(), SessionStarting()).effects == ()


@pytest.mark.timeout(5)
def test_a_command_reaches_its_handler_once_through_the_reactor(monkeypatch) -> None:
    """Claimed, so the effect is the only path — and the count is the oracle, not the outcome: a
    claim that also fell through to the Reader would run the handler twice and look identical from
    the outside for every command that happens to be idempotent."""
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    handled: list[str] = []
    monkeypatch.setattr(reader, "_handle", lambda command: handled.append(command.name))
    try:
        ipc.emit({"event": "client-message", "args": ["saitenka-help"]})
        reader._drain_events()
    finally:
        reader.close()
        gateway.close()

    assert handled == ["saitenka-help"]
    assert gateway.claim_census()["UserCommand"] == (1, 1)


@pytest.mark.parametrize("transport_lost", [False, True])
@pytest.mark.timeout(5)
def test_the_transport_decides_whether_a_claimed_command_runs(monkeypatch, transport_lost) -> None:
    """Both payloads are claimed, so this whole sequence runs inside the reactor's turn — and the
    refusal still has to happen, because it reads the connection feature the performer can see and
    the command's own reducer cannot.

    Parametrised rather than asserted once: "it was refused" is only legible against the run that
    was not.
    """
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    install_session_reactor(gateway, startup_hint=False)
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    handled: list[str] = []
    monkeypatch.setattr(reader, "_handle", lambda command: handled.append(command.name))
    try:
        if transport_lost:
            gateway.publish_session_event(ConnectionLost(0))
        ipc.emit({"event": "client-message", "args": ["saitenka-help"]})
        reader._drain_events()
        outcomes = gateway.snapshot.command_outcomes
    finally:
        reader.close()
        gateway.close()

    assert handled == ([] if transport_lost else ["saitenka-help"])
    # Only the refusal is the performer's to publish; a command that runs is reported by the
    # handler stubbed out above, so a count here would be measuring the stub.
    assert outcomes == (1 if transport_lost else 0)
