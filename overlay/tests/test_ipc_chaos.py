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
from overlay.mpvio.ipc import _MAX_RECONNECTS, MpvIPC
from overlay.mpvio.transport import UnixSocketTransport


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


def test_pump_reconnects_after_a_dropped_pipe_and_replays_observers():
    # mpv.net drops the IPC pipe mid-session but stays running: the reader hits EOF and sets the gate,
    # yet a re-dial reaches a LIVE mpv (it answers the liveness probe), so pump() recovers and replays
    # observers instead of surfacing a fatal disconnect.
    a, b = socket.socketpair()
    b.settimeout(2.0)
    ipc = MpvIPC(r"\\.\pipe\mpvsocket")
    replays: list = []
    ipc.on_reconnect = lambda: replays.append(True)
    ipc._transport = _ScriptedTransport()  # immediate EOF = dropped pipe
    ipc._start_reader()
    assert ipc._closed.wait(1.0)  # reader saw EOF
    ipc._dial = lambda _path, _timeout: UnixSocketTransport(a)
    left_before = ipc._reconnects_left

    def serve_probe() -> None:
        _recv_line(b)  # the get_property pid liveness probe
        b.sendall(b'{"error":"success","data":4242}\n')  # a live mpv answers

    th = threading.Thread(target=serve_probe)
    th.start()
    ipc.pump()  # must NOT raise — a live re-dial recovers the dropped pipe
    th.join(2.0)

    assert replays == [True]  # observers replayed after the live reconnect
    assert ipc._reconnects_left == left_before - 1
    ipc.close()
    b.close()


def test_pump_gives_up_when_a_redial_connects_but_never_replies():
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

    with pytest.raises(OSError, match="disconnected"):
        ipc.pump()
    ipc.close()


def test_pump_does_not_reconnect_after_intentional_close():
    ipc = MpvIPC("x")
    ipc._dial = lambda _p, _t: _ScriptedTransport([b'{"event":"y"}\n'])
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1.0)

    ipc.close()  # a real shutdown — must NOT reconnect

    with pytest.raises(OSError, match="disconnected"):
        ipc.pump()


def test_pump_gives_up_when_redial_keeps_failing():
    # A genuinely-gone mpv (quit): re-dials fail, so pump() consumes an attempt and surfaces the
    # disconnect (the overlay exits) instead of looping forever.
    ipc = MpvIPC("x")

    def _fail(_p, _t):
        raise OSError("pipe gone")

    ipc._dial = _fail
    ipc._transport = _ScriptedTransport()
    ipc._start_reader()
    assert ipc._closed.wait(1.0)

    with pytest.raises(OSError, match="disconnected"):
        ipc.pump()
    assert ipc._reconnects_left == _MAX_RECONNECTS - 1


def test_stale_late_reply_does_not_answer_the_next_command():
    """REGRESSION shape: a command times out, then mpv's late reply for it finally arrives. The NEXT
    command must not be handed that stale reply — ``command()``'s pre-write drain (ipc.py) exists
    exactly to prevent this LIFO-style misattribution, but it was never exercised over a real transport."""
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
        b.sendall(b'{"request_id":0,"error":"success","data":"STALE"}\n')
        deadline = time.monotonic() + 2.0
        while ipc._replies.qsize() == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert ipc._replies.qsize() == 1  # confirm it actually landed before cmd2 runs

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
