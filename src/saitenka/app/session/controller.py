"""The concrete live-session lifecycle and owner-thread loop boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.runtime.runner import SessionRunner

if TYPE_CHECKING:
    from saitenka.app.session.close_ledger import CloseLedger
    from saitenka.app.session.lifecycle import SessionLifecycle
    from saitenka.app.session.turn import SessionTurn
    from saitenka.mpvio.ipc import MpvIPC


@dataclass(frozen=True, slots=True)
class SessionGraph:
    """The validated graph consumed only by the concrete live-session boundary."""

    ipc: MpvIPC
    turn: SessionTurn
    lifecycle: SessionLifecycle

    def __post_init__(self) -> None:
        if self.turn.ipc is not self.ipc:
            raise ValueError("session turn and graph must share one mpv connection")
        if self.turn.lifecycle is not self.lifecycle:
            raise ValueError("session turn and graph must share one lifecycle")


class SessionController:
    """Own the live session state machine and its owner-thread event loop."""

    def __init__(self, graph: SessionGraph) -> None:
        self._graph = graph

    def start(self) -> None:
        """Install and start the session participants exactly once."""
        with otel_metrics.traced("startup.reader_setup"):
            self._graph.lifecycle.start()

    def pump(self, timeout: float | None = 0.0) -> bool:
        return self._graph.turn.pump(timeout)

    def run(self) -> None:
        """Start the session and drive turns until stop or transport retirement."""
        self.start()
        turn = self._graph.turn
        turn.profile_session.announce_if_ready()
        loop = self._graph.ipc.session_loop
        if loop is None:
            alive = True

            def step(timeout: float | None) -> None:
                nonlocal alive
                alive = self.pump(timeout)

            SessionRunner(step).run_until(lambda: not alive or self._graph.lifecycle.stop_requested)
            return
        loop.run(self.pump, until=lambda: self._graph.lifecycle.stop_requested)

    def request_stop(self) -> None:
        self._graph.lifecycle.request_stop()

    def close(self) -> CloseLedger:
        return self._graph.lifecycle.close()
