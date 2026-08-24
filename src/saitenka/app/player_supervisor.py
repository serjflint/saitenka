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
    from pathlib import Path

log = logging.getLogger(__name__)

#: How long mpv gets to exit on its own after being told to quit, before it is killed with its
#: children. Past this it is not exiting: the socket has already dropped and nothing is draining.
REAP_TIMEOUT_S = 5.0

#: Teardown is done and every store is flushed by the time this arms, but a lingering native thread
#: (pyo3 taffylite/resvg-py) can still keep the free-threaded interpreter from exiting, hanging the
#: quit intermittently. Daemon, so a healthy exit never waits for it.
WATCHDOG_DELAY_S = 3.0

#: Grace between the thread dump and the force-exit. The watchdog is the last thing that runs, so
#: killing first would destroy the only evidence of what it killed.
DUMP_LEAD_S = 0.5

#: Held open for the process lifetime — `faulthandler` writes to the descriptor, not the path.
_dump_file = None


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
    # `object`, not `None`: `SessionController.close` hands back its `CloseLedger` and the terminal sequence
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
    after a clean teardown, dumping every thread's stack first. Daemon, so a healthy exit never waits.

    The dump is the point. Two shutdown hangs have been diagnosed by reasoning about which thread
    *could* be holding the interpreter open; the second reasoned correctly (`cae6dff5`) and the hang
    came back anyway, with the log ending on the same line and nothing to read. `faulthandler` writes
    from a C timer thread, so it reports the stacks even once finalization has frozen the Python ones
    — including the `_python_exit` join that no Python-level hook can observe about itself.

    Nothing runs before ``os._exit`` but a raw ``write(2)``. A last resort that first has to acquire
    the logging lock is not a last resort, and the run that armed this left no warning line at all —
    which is the only reason its failure to fire was ambiguous rather than obvious.
    """
    import os

    dump_path = _arm_shutdown_dump(delay)

    def _force() -> None:
        time.sleep(delay + DUMP_LEAD_S)
        os.write(2, f"[saitenka] exit watchdog: forcing exit; threads: {dump_path}\n".encode())
        os._exit(0)

    threading.Thread(target=_force, name="saitenka-exit-watchdog", daemon=True).start()


def _arm_shutdown_dump(delay: float) -> Path | None:
    """Arm a whole-process thread dump, cancelled on a clean exit."""
    import atexit
    import faulthandler

    from saitenka.app.paths import crash_dir

    global _dump_file
    try:
        directory = crash_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / time.strftime("shutdown-hang-%Y%m%d-%H%M%S.log")
        _dump_file = path.open("w", encoding="utf-8")
    except OSError:
        log.debug("shutdown thread dump unavailable", exc_info=True)
        return None
    faulthandler.dump_traceback_later(delay, file=_dump_file, exit=False)
    atexit.register(_cancel_shutdown_dump, path)
    return path


def _cancel_shutdown_dump(path: Path) -> None:
    """`atexit` is downstream of the non-daemon join that hangs, so this runs only when the exit was
    clean — which is what makes a surviving file mean something rather than accumulate."""
    import faulthandler

    faulthandler.cancel_dump_traceback_later()
    if _dump_file is not None:
        _dump_file.close()
    if path.exists() and path.stat().st_size == 0:
        path.unlink()
