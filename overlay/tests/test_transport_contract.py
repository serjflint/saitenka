"""Transport contract for ``MpvIPC`` — the invariants EVERY transport (unix socket, Windows named
pipe, in-memory fake) must satisfy.

Post-R1-refactor this is **parametrised over the adapters**: a real ``socket.socketpair()`` wrapped in
``UnixSocketTransport`` (cross-platform — Windows emulates socketpair over AF_INET, so this runs on the
Windows executor too, unlike the AF_UNIX-bound ``test_stage9_ipc``) AND the in-memory ``FakeTransport``
(deterministic, no OS handle). The reader-thread cases run against both; the pure framing cases drive
``_feed`` directly.

Two historical Windows bugs are encoded as named, non-regressable cases:
  * ``reader_thread_drains_events_without_manual_pump`` — the single-threaded ``pump()`` was a NO-OP on
    the Windows named pipe, so nothing ever read it in steady state.
  * ``second_attached_client_independently_sees_events`` — the run-vs-attach divergence, where the
    attach path's reader silently delivered nothing.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
from util import FakeTransport

from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.transport import UnixSocketTransport


def _drain_until(ipc: MpvIPC, deadline: float = 1.0) -> list[dict]:
    """Poll ``drain_events`` until non-empty or timeout — no fixed sleep, so it won't flake under load
    yet returns the instant the reader thread has delivered."""
    end = time.monotonic() + deadline
    out: list[dict] = []
    while time.monotonic() < end:
        out += ipc.drain_events()
        if out:
            return out
        time.sleep(0.005)
    return out


def _wait_closed(ipc: MpvIPC, deadline: float = 1.0) -> None:
    end = time.monotonic() + deadline
    while not ipc._closed.is_set() and time.monotonic() < end:
        time.sleep(0.005)


# --- framing (pure; no transport) ----------------------------------------------------------------


def test_feed_reassembles_json_split_across_single_byte_chunks():
    """Worst-case fragmentation: a JSON line arriving one byte at a time (incl. a multi-byte UTF-8
    codepoint) must still parse to exactly one event. The partial-read bug MagicMock fakes hide."""
    ipc = MpvIPC("unused")
    payload = '{"event":"property-change","name":"sub-text","data":"日"}\n'.encode()
    for i in range(len(payload)):
        ipc._feed(payload[i : i + 1])
    assert [e["name"] for e in ipc.drain_events()] == ["sub-text"]


def test_feed_handles_multiple_lines_then_a_trailing_partial():
    """Several complete lines in one chunk are all emitted; a trailing incomplete line is buffered
    until its newline arrives (never dropped, never double-counted)."""
    ipc = MpvIPC("unused")
    ipc._feed(b'{"event":"a"}\n{"event":"b"}\n{"event":"c"')  # third line incomplete
    assert [e["event"] for e in ipc.drain_events()] == ["a", "b"]
    ipc._feed(b"}\n")  # completes the third
    assert [e["event"] for e in ipc.drain_events()] == ["c"]


def test_feed_skips_garbled_line_without_killing_the_stream():
    """A single unparseable line must not stop later valid events from being delivered."""
    ipc = MpvIPC("unused")
    ipc._feed(b'not json\n{"event":"ok"}\n')
    assert [e["event"] for e in ipc.drain_events()] == ["ok"]


# --- a link over one transport, for the reader-thread contract -----------------------------------


class _Link:
    """One connected client for the contract: ``ipc`` is the ``MpvIPC`` under test; the test plays
    mpv's server side via ``push`` (server→client bytes), ``next_command`` (read a line the client
    wrote), and ``disconnect`` (EOF)."""

    def __init__(self, ipc: MpvIPC, push, next_line, disconnect) -> None:
        self.ipc = ipc
        self._push = push
        self._next_line = next_line
        self._disconnect = disconnect

    def push(self, data: bytes) -> None:
        self._push(data)

    def disconnect(self) -> None:
        self._disconnect()

    def next_command(self, deadline: float = 1.0) -> dict:
        """Block until the client has written a full ``\\n``-terminated line; return it parsed."""
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            line = self._next_line()
            if line:
                return json.loads(line)
            time.sleep(0.005)
        raise AssertionError("client wrote no command line")


@pytest.fixture(params=["socketpair", "fake"])
def make_link(request):
    """Factory (call once per client) building an independent ``_Link`` over the parametrised
    transport, so the whole reader-thread contract runs against a real cross-platform socket AND the
    in-memory fake. Tears down every client + server it created."""
    made: list = []

    def factory() -> _Link:
        if request.param == "socketpair":
            client, server = socket.socketpair()
            server.setblocking(False)
            transport = UnixSocketTransport(client)
            buf = bytearray()

            def push(data, _srv=server):
                _srv.sendall(data)

            def next_line(_srv=server, _buf=buf):
                try:
                    while True:
                        chunk = _srv.recv(4096)
                        if not chunk:
                            break
                        _buf.extend(chunk)
                except BlockingIOError:
                    pass
                if b"\n" in _buf:
                    line, _, rest = bytes(_buf).partition(b"\n")
                    _buf.clear()
                    _buf.extend(rest)
                    return line
                return None

            def disconnect(_srv=server):
                _srv.close()

            extra = server
        else:
            fake = FakeTransport()
            transport = fake
            pos = [0]

            def push(data, _f=fake):
                _f.feed(data)

            def next_line(_f=fake, _pos=pos):
                data = bytes(_f.sent)
                nl = data.find(b"\n", _pos[0])
                if nl == -1:
                    return None
                line = data[_pos[0] : nl]
                _pos[0] = nl + 1
                return line

            def disconnect(_f=fake):
                _f.close()

            extra = None

        ipc = MpvIPC("unused")
        ipc._transport = transport
        ipc._start_reader()
        made.append((ipc, extra))
        return _Link(ipc, push, next_line, disconnect)

    yield factory
    for ipc, extra in made:
        ipc.close()
        if extra is not None:
            extra.close()


# --- reader thread over each transport -----------------------------------------------------------


def test_reader_thread_drains_events_without_manual_pump(make_link):
    """REGRESSION: steady state sends no commands, so the reader thread must collect events off the
    transport on its own. The old single-threaded ``pump()`` was a NO-OP on the Windows pipe → hover /
    mining / quit-detection were dead even though attach 'succeeded'."""
    link = make_link()
    link.push(b'{"event":"property-change","name":"sub-text","data":"x"}\n')
    evs = _drain_until(link.ipc)
    assert [e["name"] for e in evs if e.get("event") == "property-change"] == ["sub-text"]
    link.ipc.pump()  # not disconnected → must not raise


def test_command_reply_returns_amid_interleaved_events(make_link):
    """A command reply must come back even when async events precede it on the stream. mpv only replies
    AFTER receiving the command, so the test drives that ordering (deterministic on both transports)."""
    link = make_link()
    link.push(
        b'{"event":"property-change","name":"mouse-pos","data":{"x":1}}\n'
    )  # event ahead of reply
    out: dict = {}

    def call():
        out["reply"] = link.ipc.command("get_property", "time-pos", timeout=2.0)

    t = threading.Thread(target=call)
    t.start()
    cmd = link.next_command()  # mpv receives the command...
    assert cmd["command"] == ["get_property", "time-pos"]
    link.push(b'{"data":0.0,"request_id":0,"error":"success"}\n')  # ...then replies
    t.join(3)
    assert out["reply"].get("error") == "success"
    assert any(e.get("name") == "mouse-pos" for e in _drain_until(link.ipc))


def test_second_attached_client_independently_sees_events(make_link):
    """REGRESSION (run-vs-attach): a second client attached to mpv must independently receive its own
    events. Two clients, each on its own transport, each drains what its server side pushed — neither
    silently delivers nothing."""
    a = make_link()
    b = make_link()
    a.push(b'{"event":"property-change","name":"pause","data":true}\n')
    b.push(b'{"event":"property-change","name":"sub-text","data":"y"}\n')
    assert any(e.get("name") == "pause" for e in _drain_until(a.ipc))
    assert any(e.get("name") == "sub-text" for e in _drain_until(b.ipc))


def test_close_unblocks_reader_and_command_reports_disconnect(make_link):
    """EOF (mpv quit / pipe closed) must unblock the reader, make ``pump()`` raise, and make a
    subsequent ``command()`` return a disconnect result rather than hang."""
    link = make_link()
    link.disconnect()  # EOF on the client's transport
    _wait_closed(link.ipc)
    with pytest.raises(OSError):
        link.ipc.pump()
    assert link.ipc.command("get_property", "anything").get("error") == "disconnected"
