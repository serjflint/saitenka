"""Transport contract for ``MpvIPC`` — the invariants EVERY transport (unix socket, Windows named
pipe, in-memory fake) must satisfy.

This is **R1 step 1 (test-first)**: it pins today's behaviour BEFORE the port/adapter refactor, so the
refactor is provably behaviour-preserving — after it, the same assertions get parametrised over the
``Fake``/``Unix``/``NamedPipe`` adapters. It drives the framing logic directly (``_feed``) and the
reader thread over ``socket.socketpair()`` — which is **cross-platform** (Windows emulates it over
AF_INET), so unlike ``test_stage9_ipc`` (AF_UNIX server, POSIX-only) this suite also runs on the
Windows executor and exercises the shared reader/framing code there.

Two historical Windows bugs are encoded as named, non-regressable cases:
  * ``reader_thread_drains_events_without_manual_pump`` — the single-threaded ``pump()`` was a NO-OP on
    the Windows named pipe, so nothing ever read it in steady state.
  * ``second_attached_client_independently_sees_events`` — the run-vs-attach divergence, where the
    attach path's reader silently delivered nothing.

(It reaches into ``_sock``/``_feed``/``_start_reader``/``_closed`` — the very seam R1 formalises into a
``Transport`` protocol; the private access is temporary scaffolding the refactor replaces.)
"""

from __future__ import annotations

import socket
import time

import pytest

from overlay.mpvio.ipc import MpvIPC


def _client() -> tuple[MpvIPC, socket.socket]:
    """An ``MpvIPC`` wired to one end of a socketpair with its reader running — what ``connect()`` does
    minus the OS-specific dialling. Returns ``(ipc, server_end)`` where the caller writes mpv's side to
    ``server_end``."""
    a, b = socket.socketpair()
    ipc = MpvIPC("unused")
    ipc._sock = a
    ipc._start_reader()
    return ipc, b


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


# --- reader thread over a real (cross-platform) transport ----------------------------------------


def test_reader_thread_drains_events_without_manual_pump():
    """REGRESSION: steady state sends no commands, so the reader thread must collect events off the
    transport on its own. The old single-threaded ``pump()`` was a NO-OP on the Windows pipe → hover /
    mining / quit-detection were all dead even though attach 'succeeded'."""
    ipc, server = _client()
    try:
        server.sendall(b'{"event":"property-change","name":"sub-text","data":"x"}\n')
        evs = _drain_until(ipc)
        assert [e["name"] for e in evs if e.get("event") == "property-change"] == ["sub-text"]
        ipc.pump()  # not disconnected → must not raise
    finally:
        server.close()
        ipc.close()


def test_command_reply_returns_while_events_interleave():
    """A command reply must come back even when async events are interleaved ahead of it on the
    stream (single-flight reply channel vs. the event list)."""
    ipc, server = _client()
    try:
        server.sendall(
            b'{"event":"property-change","name":"mouse-pos","data":{"x":1}}\n'
            b'{"data":0.0,"request_id":0,"error":"success"}\n'
        )
        reply = ipc.command("get_property", "time-pos")
        assert reply.get("error") == "success"
        assert any(e.get("name") == "mouse-pos" for e in _drain_until(ipc))
    finally:
        server.close()
        ipc.close()


def test_second_attached_client_independently_sees_events():
    """REGRESSION (run-vs-attach): a second client attached to mpv must independently receive its own
    events. Two clients, each on its own transport, each drains what its server side pushed — neither
    silently delivers nothing."""
    a_ipc, a_srv = _client()
    b_ipc, b_srv = _client()
    try:
        a_srv.sendall(b'{"event":"property-change","name":"pause","data":true}\n')
        b_srv.sendall(b'{"event":"property-change","name":"sub-text","data":"y"}\n')
        assert any(e.get("name") == "pause" for e in _drain_until(a_ipc))
        assert any(e.get("name") == "sub-text" for e in _drain_until(b_ipc))
    finally:
        a_srv.close()
        b_srv.close()
        a_ipc.close()
        b_ipc.close()


def test_close_unblocks_reader_and_command_reports_disconnect():
    """EOF (mpv quit / pipe closed) must unblock the reader, make ``pump()`` raise, and make a
    subsequent ``command()`` return a disconnect result rather than hang."""
    ipc, server = _client()
    server.close()  # EOF on the client's transport
    _wait_closed(ipc)
    with pytest.raises(OSError):
        ipc.pump()
    assert ipc.command("get_property", "anything").get("error") == "disconnected"
    ipc.close()
