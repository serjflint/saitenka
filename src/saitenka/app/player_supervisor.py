"""The entrypoint's terminal sequence for the mpv process and its transport.

This is the *entrypoint's* duty, not the session's. `ClosePhase` retires what the session owns; the
player's lifetime and the thread it runs on belong to whoever started — or merely connected to — mpv,
and there is no phase for "the process is exiting". So the sequence lives here rather than in the
close ledger, and it is **declared** (`OWNED`, `ATTACHED`) rather than spelled out inline, for the
same reason the close phases are: two entrypoints hand-writing the same order is one edit away from
disagreeing, with nothing at the seam to notice.

The two differ by *capability*, not by sequence. An entrypoint that started mpv can quit it, reap it
and force-kill it; one that attached to a running mpv must not — detaching leaves the user's player
running, which is the whole contract of `attach`. That difference is structural here: an attached
supervisor holds no process at all, so there is nothing for a later edit to reach for.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)

#: How long mpv gets to exit on its own after being told to quit, before it is killed with its
#: children. Past this it is not exiting: the socket has already dropped and nothing is draining.
REAP_TIMEOUT_S = 5.0

#: Teardown is done and every store is flushed by the time this arms, but a lingering native thread
#: (pyo3 taffylite/resvglite) can still keep the free-threaded interpreter from exiting, hanging the
#: quit intermittently. Daemon, so a healthy exit never waits for it.
WATCHDOG_DELAY_S = 3.0


class TerminalStep(StrEnum):
    """One step of the shutdown, named by what it retires."""

    SESSION_CLOSE = "session-close"
    PLAYER_QUIT = "player-quit"
    TRANSPORT_CLOSE = "transport-close"
    PLAYER_REAP = "player-reap"
    EXIT_WATCHDOG = "exit-watchdog"


#: The declaration. Order is the contract — the quit has to reach mpv before the transport carrying
#: it closes, and the reap has to follow the quit or it times out on a player nobody asked to leave.
OWNED: tuple[TerminalStep, ...] = (
    TerminalStep.SESSION_CLOSE,
    TerminalStep.PLAYER_QUIT,
    TerminalStep.TRANSPORT_CLOSE,
    TerminalStep.PLAYER_REAP,
    TerminalStep.EXIT_WATCHDOG,
)

ATTACHED: tuple[TerminalStep, ...] = (
    TerminalStep.SESSION_CLOSE,
    TerminalStep.TRANSPORT_CLOSE,
)


class Session(Protocol):
    # `object`, not `None`: `Reader.close` hands back its `CloseLedger` and the terminal sequence
    # deliberately does not read it — what a step returns is between it and its own callers.
    def close(self) -> object: ...


class Transport(Protocol):
    def command(self, *args: object) -> object: ...
    def close(self) -> object: ...


@dataclass(frozen=True, slots=True)
class PlayerSupervisor:
    """Runs one of the two declared sequences. `process` is `None` for an attached player."""

    steps: tuple[TerminalStep, ...]
    process: subprocess.Popen[bytes] | None = None
    on_exit_code: Callable[[int | None], None] | None = None

    @classmethod
    def owned(
        cls,
        process: subprocess.Popen[bytes],
        *,
        on_exit_code: Callable[[int | None], None] | None = None,
    ) -> PlayerSupervisor:
        return cls(OWNED, process, on_exit_code)

    @classmethod
    def attached(cls) -> PlayerSupervisor:
        """No process — an attached entrypoint has no process-control capability by construction."""
        return cls(ATTACHED)

    def finalize(self, session: Session, transport: Transport) -> None:
        """Perform the declared sequence, isolating each step.

        Each step is isolated on purpose. Teardown must continue at all costs: these used to share
        one `try`, so a `session.close()` that raised also skipped the quit and the transport close
        — leaving mpv running and the socket open on exactly the path where something already went
        wrong. Setup is the opposite and stays that way; this is the retiring half.
        """
        for step in self.steps:
            try:
                self._perform(step, session, transport)
            except Exception:
                log.debug("terminal step %s failed", step, exc_info=True)

    def _perform(self, step: TerminalStep, session: Session, transport: Transport) -> None:
        match step:
            case TerminalStep.SESSION_CLOSE:
                session.close()
            case TerminalStep.PLAYER_QUIT:
                # Stays direct: the reactor is stopping, so a correlated quit could never be drained.
                transport.command("quit")
            case TerminalStep.TRANSPORT_CLOSE:
                transport.close()
            case TerminalStep.PLAYER_REAP:
                self._reap()
            case TerminalStep.EXIT_WATCHDOG:
                arm_exit_watchdog(WATCHDOG_DELAY_S)

    def _reap(self) -> None:
        assert self.process is not None  # only the owned sequence carries this step
        try:
            self.process.wait(timeout=REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            from saitenka.app.procutil import kill_process_tree

            kill_process_tree(self.process)  # didn't quit → kill it + its children (no orphans)
            return
        if self.on_exit_code is not None:
            self.on_exit_code(self.process.returncode)  # a self-exit, never our force-kill


def arm_exit_watchdog(delay: float) -> None:
    """Force process exit ``delay`` s from now if a stray/native thread stalls interpreter shutdown
    after a clean teardown. Daemon, so it never delays a healthy exit."""
    import os

    def _force() -> None:
        time.sleep(delay)
        log.warning("exit watchdog: interpreter did not exit %.1fs after teardown — forcing", delay)
        os._exit(0)

    threading.Thread(target=_force, name="saitenka-exit-watchdog", daemon=True).start()
