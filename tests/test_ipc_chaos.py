"""Wave-2 P2: chaos scenarios for ``MpvIPC`` that ``test_transport_contract.py`` doesn't cover — write
failures and reply staleness, the two ingredients behind the historical Windows-hang bug class (a write
that silently drops, or a stale reply answering the wrong command). Real ``socket.socketpair()`` end to
end; write faults are injected via the ``Transport`` port (the same seam ``FakeTransport`` uses) rather
than raced against the OS, so these are deterministic.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from saitenka.mpvio.ipc import MpvIPC
from saitenka.mpvio.transport import UnixSocketTransport


class _FlakyWriteTransport:
    """Wraps a real transport; the ``fail_after``-th ``write`` call onward raises, simulating a pipe
    that breaks mid-conversation (``BrokenPipeError`` / ``ECONNRESET``) without racing the OS."""

    def __init__(self, inner, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self._calls = 0

    def read(self, n: int) -> bytes:
        return self._inner.read(n)

    def write(self, data: bytes) -> None:
        self._calls += 1
        if self._calls > self._fail_after:
            raise BrokenPipeError("simulated broken pipe")
        self._inner.write(data)

    def close(self) -> None:
        self._inner.close()


def _recv_line(sock: socket.socket) -> dict:
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError("peer closed before sending a full command line")
        buf += chunk
    line, _, _ = buf.partition(b"\n")
    return json.loads(line.decode())


def test_broken_pipe_on_first_write_marks_disconnected_not_hang():
    """A write that fails outright (pipe already broken) must return a disconnect result immediately —
    never hang waiting on a reply that can never come."""
    a, b = socket.socketpair()
    ipc = MpvIPC("unused")
    ipc._transport = _FlakyWriteTransport(UnixSocketTransport(a), fail_after=0)
    ipc._start_reader()
    try:
        result = ipc.command("get_property", "time-pos", timeout=1.0)
        assert result == {"error": "disconnected"}
        assert ipc._closed.is_set()
        # a follow-up command must also fail fast, not hang
        assert ipc.command("quit") == {"error": "disconnected"}
    finally:
        ipc.close()
        b.close()


def test_async_submission_does_not_wait_for_a_blocked_transport_write():
    class BlockedWriteTransport:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def write(self, _data: bytes) -> None:
            self.entered.set()
            self.release.wait(2)

        def read(self, _n: int) -> bytes:
            self.release.wait(2)
            return b""

        def close(self) -> None:
            self.release.set()

    transport = BlockedWriteTransport()
    ipc = MpvIPC("unused")
    ipc._transport = transport
    ipc._start_reader()
    try:
        started = time.monotonic()
        request = ipc.command_async("show-text", "hover", 1)
        elapsed = time.monotonic() - started

        assert elapsed < 0.05
        assert transport.entered.wait(1)
        assert not request.future.done()
    finally:
        transport.release.set()
        ipc.close()


@pytest.mark.timeout(5)
def test_retired_write_failure_cannot_close_the_replacement_connection():
    class BlockingFailureTransport:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def write(self, _data: bytes) -> None:
            self.entered.set()
            self.release.wait(2)
            raise BrokenPipeError("retired pipe")

        def read(self, _n: int) -> bytes:
            self.release.wait(2)
            return b""

        def close(self) -> None:
            pass

    class StableTransport:
        def __init__(self) -> None:
            self.release = threading.Event()

        def write(self, _data: bytes) -> None:
            pass

        def read(self, _n: int) -> bytes:
            self.release.wait(2)
            return b""

        def close(self) -> None:
            self.release.set()

    old = BlockingFailureTransport()
    replacement = StableTransport()
    ipc = MpvIPC("unused")
    ipc._transport = old
    ipc._start_reader()
    request = ipc.command_async("show-text", "old", 1)
    assert old.entered.wait(1)

    installed, retired = ipc._install_replacement(replacement)
    assert installed
    ipc._resolve_pending(retired, {"error": "disconnected"})
    replacement_closed = ipc._closed
    old.release.set()
    assert request.future.result(timeout=1) == {"error": "disconnected"}
    assert not replacement_closed.wait(0.05)

    ipc.close()


def test_write_failure_mid_conversation_marks_disconnected():
    """The pipe can break AFTER a prior command already succeeded (mpv quitting mid-session). The next
    write must surface a clean disconnect, not raise out of ``command()`` or hang."""
    a, b = socket.socketpair()
    ipc = MpvIPC("unused")
    ipc._transport = _FlakyWriteTransport(UnixSocketTransport(a), fail_after=1)
    ipc._start_reader()
    b.settimeout(2.0)
    try:

        def serve_one() -> None:
            _recv_line(b)
            b.sendall(b'{"request_id":0,"error":"success"}\n')

        th = threading.Thread(target=serve_one)
        th.start()
        r1 = ipc.command("get_property", "pause", timeout=2.0)
        th.join(2.0)
        assert r1.get("error") == "success"

        r2 = ipc.command("get_property", "time-pos", timeout=1.0)  # write raises this time
        assert r2 == {"error": "disconnected"}
        assert ipc._closed.is_set()
    finally:
        ipc.close()
        b.close()


class _ScriptedTransport:
    """Delivers queued byte chunks then EOF (``b""``) — a pipe that drops after its data. Reader-only;
    writes are ignored. Used to drive the auto-reconnect path deterministically (no OS race)."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])
        self.closed = False

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def write(self, _data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_reconnect_once_replaces_a_dropped_live_pipe():
    # mpv.net drops the IPC pipe mid-session but stays running: the reader hits EOF and sets the gate,
    # yet a re-dial reaches a LIVE mpv and answers the liveness probe.
    a, b = socket.socketpair()
    b.settimeout(2.0)
    ipc = MpvIPC(r"\\.\pipe\mpvsocket")
    ipc._transport = _ScriptedTransport()  # immediate EOF = dropped pipe
    ipc._start_reader()
    assert ipc._closed.wait(1.0)  # reader saw EOF
    ipc._dial = lambda _path, _timeout: UnixSocketTransport(a)
    left_before = ipc._reconnects_left

    def serve_probe() -> None:
        command = _recv_line(b)  # the get_property pid liveness probe
        b.sendall(
            json.dumps(
                {
                    "request_id": command["request_id"],
                    "error": "success",
                    "data": 4242,
                }
            ).encode()
            + b"\n"
        )  # a live mpv answers

    th = threading.Thread(target=serve_probe)
    th.start()
    assert ipc.reconnect_once()
    th.join(2.0)

    assert ipc._reconnects_left == left_before - 1
    assert ipc.drain_events() == []
    ipc.close()
    b.close()


@pytest.mark.timeout(5)
def test_reader_from_old_epoch_cannot_close_the_replacement_connection():
    class StubbornTransport:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def read(self, _size: int) -> bytes:
            self.entered.set()
            if not self.release.wait(4):
                raise TimeoutError("test did not release the old reader")
            return b""

        def write(self, _data: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    old = StubbornTransport()
    a, b = socket.socketpair()
    b.settimeout(3.0)
    ipc = MpvIPC("unused")
    ipc._transport = old
    ipc._start_reader()
    old_reader = ipc._reader
    if old_reader is None:  # pragma: no cover - _start_reader always installs it
        pytest.fail("reader thread was not installed")
    assert old.entered.wait(1)
    ipc._closed.set()
    ipc._dial = lambda _path, _timeout: UnixSocketTransport(a)

    def serve_probe() -> None:
        command = _recv_line(b)
        b.sendall(
            json.dumps(
                {"request_id": command["request_id"], "error": "success", "data": 1}
            ).encode()
            + b"\n"
        )

    probe = threading.Thread(target=serve_probe)
    probe.start()
    assert ipc.reconnect_once()
    probe.join(2)

    old.release.set()
    old_reader.join(2)
    assert not old_reader.is_alive()

    followup = ipc.command_async("get_property", "pid")
    followup_command = _recv_line(b)
    b.sendall(
        json.dumps(
            {"request_id": followup_command["request_id"], "error": "success", "data": 2}
        ).encode()
        + b"\n"
    )
    assert followup.future.result(timeout=1)["data"] == 2

    ipc.close()
    b.close()


@pytest.mark.timeout(5)
def test_close_wins_over_an_inflight_reconnect_dial():
    dial_started = threading.Event()
    release_dial = threading.Event()
    replacement = _ScriptedTransport([b"{}\n"])
    ipc = MpvIPC("unused")
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1)

    def dial(_path: str, _timeout: float):
        dial_started.set()
        if not release_dial.wait(3):
            raise TimeoutError("test did not release reconnect dial")
        return replacement

    ipc._dial = dial
    outcomes = []

    def reconnect() -> None:
        if not ipc.reconnect_once():
            outcomes.append("disconnected")

    thread = threading.Thread(target=reconnect)
    thread.start()
    assert dial_started.wait(1)
    ipc.close()
    release_dial.set()
    thread.join(2)

    assert outcomes == ["disconnected"]
    assert replacement.closed is True
    assert ipc._transport is None and ipc._closed.is_set()


@pytest.mark.timeout(5)
def test_command_submitted_during_reconnect_cannot_cross_connection_epochs():
    class GateLock:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def __enter__(self):
            self.entered.set()
            if not self.release.wait(3):
                raise TimeoutError("test did not release reconnect publication")

        def __exit__(self, *_exc) -> None:
            pass

    class ReplyingTransport:
        def __init__(self) -> None:
            self.commands: list[list] = []
            self.replies: list[bytes] = []
            self.ready = threading.Event()
            self.closed = False

        def write(self, data: bytes) -> None:
            message = json.loads(data)
            self.commands.append(message["command"])
            if message["command"] == ["get_property", "pid"]:
                self.replies.append(
                    json.dumps(
                        {"request_id": message["request_id"], "error": "success", "data": 1}
                    ).encode()
                    + b"\n"
                )
                self.ready.set()

        def read(self, _size: int) -> bytes:
            self.ready.wait(3)
            if self.closed:
                return b""
            self.ready.clear()
            return self.replies.pop(0)

        def close(self) -> None:
            self.closed = True
            self.ready.set()

    gate = GateLock()
    replacement = ReplyingTransport()
    ipc = MpvIPC("unused")
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1)
    ipc._events_lock = gate
    ipc._dial = lambda _path, _timeout: replacement
    reconnected = []

    def reconnect() -> None:
        reconnected.append(ipc.reconnect_once())

    reconnect_thread = threading.Thread(target=reconnect)
    reconnect_thread.start()
    assert gate.entered.wait(1)

    submitted = []
    submit_thread = threading.Thread(
        target=lambda: submitted.append(ipc.command_async("show-text", "late", 1))
    )
    submit_thread.start()
    assert not submitted
    gate.release.set()
    submit_thread.join(2)
    reconnect_thread.join(2)

    assert submitted[0].future.result(timeout=1) == {"error": "stale-epoch"}
    assert replacement.commands == [["get_property", "pid"]]
    assert reconnected == [True]
    ipc.close()


def test_reconnect_once_rejects_a_redial_that_never_replies():
    # REGRESSION (2.0.1): a self-launched mpv that QUIT leaves a socket that can still accept a connect
    # yet never replies. Without the liveness probe pump() declared that a "reconnect" and hung the
    # poll loop for command()'s full timeout, _MAX_RECONNECTS times over. The probe must detect the
    # dead endpoint and raise, so the overlay exits promptly on quit instead of zombie-hanging.
    a, b = socket.socketpair()
    b.close()  # peer gone → reads on `a` hit EOF = a re-dialed-but-dead endpoint (mpv quit)
    ipc = MpvIPC("x")
    ipc._dial = lambda _p, _t: UnixSocketTransport(a)
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1.0)

    assert not ipc.reconnect_once()
    ipc.close()


def test_reconnect_once_does_not_run_after_intentional_close():
    ipc = MpvIPC("x")
    ipc._dial = lambda _p, _t: _ScriptedTransport([b'{"event":"y"}\n'])
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1.0)

    ipc.close()  # a real shutdown — must NOT reconnect

    assert not ipc.reconnect_once()


def test_a_refused_endpoint_spends_the_whole_reconnect_budget_at_once():
    """A genuinely-gone mpv (quit): nothing is listening, so no later dial can succeed either.

    The budget exists for mpv.net's pipe vanishing while mpv RUNS — a case where the next dial
    connects. Spending it one attempt at a time here bought ~7s of ECONNREFUSED between mpv quitting
    and saitenka exiting, which reads as a hang; an exhausted budget is what closes the session.
    """
    ipc = MpvIPC("x")

    def _fail(_p, _t):
        raise ConnectionRefusedError(61, "Connection refused")

    ipc._dial = _fail
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1.0)

    assert not ipc.reconnect_once()
    assert ipc.reconnects_left == 0


def test_stale_late_reply_does_not_answer_the_next_command():
    """REGRESSION shape: a command times out, then mpv's late reply for it finally arrives. The NEXT
    command must not be handed that stale reply: request IDs make the late reply unaddressable after
    its timed-out pending request was removed."""
    a, b = socket.socketpair()
    ipc = MpvIPC("unused")
    ipc._transport = UnixSocketTransport(a)
    ipc._start_reader()
    b.settimeout(2.0)
    try:
        cmd1_holder: dict = {}

        def read_cmd1() -> None:
            cmd1_holder["cmd"] = _recv_line(b)

        th1 = threading.Thread(target=read_cmd1)
        th1.start()
        r1 = ipc.command(
            "get_property", "sub-start", timeout=0.3
        )  # server withholds reply → timeout
        th1.join(2.0)
        assert r1 == {"error": "timeout"}
        assert cmd1_holder["cmd"]["command"] == ["get_property", "sub-start"]

        # the stale reply for cmd1 shows up late
        bytes_before = ipc._bytes_read
        b.sendall(b'{"request_id":0,"error":"success","data":"STALE"}\n')
        deadline = time.monotonic() + 2.0
        while ipc._bytes_read == bytes_before and time.monotonic() < deadline:
            time.sleep(0.005)
        assert ipc._bytes_read > bytes_before
        assert 0 not in ipc._pending

        cmd2_holder: dict = {}

        def read_cmd2() -> None:
            cmd2_holder["cmd"] = _recv_line(b)
            b.sendall(b'{"request_id":1,"error":"success","data":"FRESH"}\n')

        th2 = threading.Thread(target=read_cmd2)
        th2.start()
        r2 = ipc.command("get_property", "time-pos", timeout=2.0)
        th2.join(2.0)
        assert r2.get("data") == "FRESH"  # not the stale STALE reply left over from cmd1
    finally:
        ipc.close()
        b.close()
