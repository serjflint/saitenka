"""The entrypoint's terminal sequence: what each capability retires, and in what order.

The order used to live in two `finally` blocks that had to agree by hand. It is a declaration now
(`player_supervisor.OWNED` / `ATTACHED`), so these assert against the declaration and against what a
run through it actually performs — the two together are what makes "both paths read the same
sequence" checkable rather than asserted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest
from util import FakeIPC

from saitenka.app.player_supervisor import (
    ATTACHED,
    OWNED,
    PlayerSupervisor,
    TerminalStep,
)


class FakeSession:
    def __init__(self, log: list[str], *, fail: bool = False) -> None:
        self._log, self._fail = log, fail

    def close(self) -> None:
        self._log.append("session.close")
        if self._fail:
            raise RuntimeError("close blew up")


class FakeTransport(FakeIPC):
    """The shared fake, with the terminal steps written into the shared ordering log.

    Inherits rather than reimplements: what these tests assert is *when* the transport is told to
    quit relative to the session and the reap, and a narrow double would answer that while quietly
    taking a different branch anywhere the supervisor grows a second call.
    """

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self._log = log

    def command(self, *args: object) -> object:
        self._log.append(f"transport.command{list(args)}")
        return super().command(*args)

    def close(self) -> None:
        self._log.append("transport.close")


class FakeProcess:
    """Enough of `Popen` for the reap step. `quits=False` is an mpv that ignores the quit."""

    def __init__(self, log: list[str], *, quits: bool = True, returncode: int | None = 0) -> None:
        self._log, self._quits = log, quits
        self.returncode = returncode

    def wait(self, timeout: float | None = None) -> int:
        self._log.append("process.wait")
        if not self._quits:
            raise subprocess.TimeoutExpired("mpv", timeout or 0)
        return self.returncode or 0


@pytest.fixture(autouse=True)
def _no_watchdog(monkeypatch):
    """The watchdog force-exits the interpreter — never arm the real one under test."""
    monkeypatch.setattr("saitenka.app.player_supervisor.arm_exit_watchdog", lambda _delay: None)


def test_owned_run_quits_and_kills_before_arming_the_watchdog(monkeypatch):
    log: list[str] = []
    killed: list[object] = []
    monkeypatch.setattr("saitenka.app.procutil.kill_process_tree", killed.append)
    armed: list[float] = []
    monkeypatch.setattr("saitenka.app.player_supervisor.arm_exit_watchdog", armed.append)
    process = FakeProcess(log, quits=False)

    PlayerSupervisor.owned(process).finalize(FakeSession(log), FakeTransport(log))

    assert log == [
        "session.close",
        "transport.command['quit']",
        "transport.close",
        "process.wait",
    ]
    assert killed == [process], "mpv that ignored the quit is killed with its children"
    assert armed, "the watchdog arms only after the player is gone"


def test_attach_has_no_process_control_capability():
    log: list[str] = []
    supervisor = PlayerSupervisor.attached()

    supervisor.finalize(FakeSession(log), FakeTransport(log))

    # Structural, not conditional: an attached supervisor holds no process, so there is nothing a
    # later edit could reach for. Detaching must leave the user's mpv running.
    assert supervisor.process is None
    assert log == ["session.close", "transport.close"]
    assert TerminalStep.PLAYER_QUIT not in ATTACHED
    assert TerminalStep.PLAYER_REAP not in ATTACHED


def test_the_owned_sequence_declares_quit_before_the_transport_that_carries_it():
    """Order is the contract: the quit has to reach mpv through a transport that is still open, and
    the reap has to follow the quit or it times out on a player nobody asked to leave."""
    order = {step: i for i, step in enumerate(OWNED)}

    assert order[TerminalStep.SESSION_CLOSE] < order[TerminalStep.PLAYER_QUIT]
    assert order[TerminalStep.PLAYER_QUIT] < order[TerminalStep.TRANSPORT_CLOSE]
    assert order[TerminalStep.PLAYER_QUIT] < order[TerminalStep.PLAYER_REAP]
    assert order[TerminalStep.PLAYER_REAP] < order[TerminalStep.EXIT_WATCHDOG]
    assert set(ATTACHED) < set(OWNED), "attached is a capability subset, not a different sequence"


def test_a_failing_step_does_not_skip_the_rest_of_the_teardown():
    """Teardown continues at all costs. These shared one `try` before, so a `close()` that raised
    also skipped the quit and the transport close — leaving mpv running and the socket open on
    exactly the path where something had already gone wrong."""
    log: list[str] = []

    PlayerSupervisor.attached().finalize(FakeSession(log, fail=True), FakeTransport(log))

    assert log == ["session.close", "transport.close"]


def test_a_clean_exit_reports_the_players_own_status():
    """mpv's crashes look identical to a clean quit from the socket's side, so the exit code is the
    only thing that tells them apart — and it is meaningful only when mpv left on its own."""
    log: list[str] = []
    seen: list[int | None] = []

    PlayerSupervisor.owned(FakeProcess(log, returncode=-11), on_exit_code=seen.append).finalize(
        FakeSession(log), FakeTransport(log)
    )

    assert seen == [-11]


def test_a_force_killed_player_reports_no_exit_status(monkeypatch):
    monkeypatch.setattr("saitenka.app.procutil.kill_process_tree", lambda _proc: None)
    log: list[str] = []
    seen: list[int | None] = []

    PlayerSupervisor.owned(FakeProcess(log, quits=False), on_exit_code=seen.append).finalize(
        FakeSession(log), FakeTransport(log)
    )

    assert seen == [], "the kill was ours; reporting it as mpv's exit would be a false crash report"


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_the_watchdog_dumps_every_thread_and_forces_exit_on_a_hung_shutdown(tmp_path):
    """A real interpreter, really hung, because that is the only state the device exists for.

    In-process this cannot be tested: the assertion is about what happens *after* the test process
    would have to have exited. The hang is the shape that shipped — a non-daemon pool worker whose
    `concurrent.futures` atexit join outlives every close phase — and the dump has to name it from a
    C timer thread, since by then the Python ones are frozen in finalization.
    """
    program = textwrap.dedent("""
        import concurrent.futures, sys, time
        from saitenka.app.player_supervisor import arm_exit_watchdog
        concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(time.sleep, 60)
        arm_exit_watchdog(1.0)
        print("armed", flush=True)
    """)
    env = {**os.environ, "SAITENKA_CACHE_DIR": str(tmp_path / "cache")}
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
        timeout=25,
        env=env,  # explicit: the dump location is this test's precondition, not conftest's
    )
    elapsed = time.monotonic() - started

    assert done.returncode == 0
    assert elapsed < 20, "the join would have held this for 60s without the watchdog"
    assert "exit watchdog: forcing exit" in done.stderr, "the reason reaches the terminal"

    dumps = list((tmp_path / "cache" / "crashes").glob("shutdown-hang-*.log"))
    assert len(dumps) == 1, "a surviving dump is the record that this exit hung"
    text = dumps[0].read_text()
    assert "_python_exit" in text, "the dump names the join nothing on the Python side can observe"
    if sys.version_info >= (3, 14):
        # faulthandler only started labelling threads with their name in 3.14 — measured, not assumed:
        # 3.13 writes a bare `Thread 0x...` header, so the worker is in the dump but unnameable there.
        # A prefix, not the whole name: it prints the OS thread name, and Linux caps that at 15 bytes —
        # the same truncation that turns our own `saitenka-exit-watchdog` into `saitenka-exit-w`.
        assert "ThreadPoolExecu" in text, "…and the worker it is waiting on"


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_a_clean_exit_leaves_no_shutdown_dump_behind(tmp_path):
    """The negative control for the file's meaning: if a healthy run also left one, its presence in
    a report would say nothing."""
    program = textwrap.dedent("""
        from saitenka.app.player_supervisor import arm_exit_watchdog
        arm_exit_watchdog(30.0)
    """)
    env = {**os.environ, "SAITENKA_CACHE_DIR": str(tmp_path / "cache")}
    done = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
        timeout=25,
        env=env,  # explicit: the dump location is this test's precondition, not conftest's
    )

    assert done.returncode == 0
    assert not list((tmp_path / "cache" / "crashes").glob("shutdown-hang-*.log"))
