"""The concrete live-session lifecycle and owner-thread loop boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.runtime.runner import SessionRunner

if TYPE_CHECKING:
    from saitenka.app.session.close_ledger import CloseLedger
    from saitenka.app.session.turn import SessionTurn


class SessionController:
    """Own the live session state machine and its owner-thread event loop."""

    def __init__(self, turn: SessionTurn) -> None:
        self._turn = turn

    def start(self) -> None:
        """Install and start the session participants exactly once."""
        with otel_metrics.traced("startup.reader_setup"):
            self._turn.lifecycle.start()

    def pump(self, timeout: float | None = 0.0) -> bool:
        return self._turn.pump(timeout)

    def run(self) -> None:
        """Start the session and drive turns until stop or transport retirement."""
        self.start()
        loop = self._turn.ipc.session_loop
        if loop is None:
            alive = True

            def step(timeout: float | None) -> None:
                nonlocal alive
                alive = self.pump(timeout)

            SessionRunner(step).run_until(lambda: not alive or self._turn.lifecycle.stop_requested)
            return
        loop.run(self.pump, until=lambda: self._turn.lifecycle.stop_requested)

    def request_stop(self) -> None:
        self._turn.lifecycle.request_stop()

    def close(self) -> CloseLedger:
        return self._turn.lifecycle.close()
