"""Stage 9–12: mpv IPC wire format + BGRA conversion (no mpv needed — a fake AF_UNIX server)."""

import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.osd import Overlay, to_bgra

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX fake server is POSIX-only")


def _fake_mpv(path, received, ready):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    line, _, _ = buf.partition(b"\n")
    received.append(json.loads(line.decode()))
    conn.sendall(b'{"request_id":0,"error":"success"}\n')
    conn.close()
    srv.close()


def test_overlay_add_wire_format():
    # AF_UNIX sun_path is capped (104 on macOS); keep the socket path short (not under tmp_path).
    sock = f"/tmp/sait-osd-{os.getpid()}.sock"
    if os.path.exists(sock):
        os.unlink(sock)
    received: list = []
    ready = threading.Event()
    th = threading.Thread(target=_fake_mpv, args=(sock, received, ready))
    th.start()
    assert ready.wait(3)

    ipc = MpvIPC(sock).connect(timeout=5)
    ov = Overlay(ipc)
    reply = ov.show(Image.new("RGBA", (10, 4), (255, 0, 0, 255)), x=7, y=9, oid=1)
    assert reply.get("error") == "success"
    th.join(3)
    ipc.close()
    if os.path.exists(sock):
        os.unlink(sock)

    cmd = received[0]["command"]
    assert cmd[0] == "overlay-add"
    assert cmd[1] == 1  # id
    assert cmd[2] == 7 and cmd[3] == 9  # x, y
    assert cmd[5] == 0  # offset
    assert cmd[6] == "bgra"
    assert cmd[7] == 10 and cmd[8] == 4  # w, h
    assert cmd[9] == 40  # stride = w*4
    assert Path(cmd[4]).stat().st_size == 10 * 4 * 4  # BGRA bytes on disk


# --- Stage 3: MpvIPC.command timeout and error checking ------------------------------------------


def _fake_mpv_slow(path, ready, delay=0.5):
    """A server that waits before replying (for timeout testing)."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    time_mod = __import__("time")
    time_mod.sleep(delay)
    conn.sendall(b'{"request_id":0,"error":"success"}\n')
    conn.close()
    srv.close()


def _fake_mpv_error(path, ready, error="property unavailable"):
    """A server that replies with a non-success error."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    conn.sendall(f'{{"request_id":0,"error":"{error}"}}\n'.encode())
    conn.close()
    srv.close()


def test_command_raises_on_error_response():
    """MpvIPC.command must raise (or return an error indicator) when mpv replies with a non-success error."""
    sock = f"/tmp/sait-err-{os.getpid()}.sock"
    if os.path.exists(sock):
        os.unlink(sock)
    ready = threading.Event()
    th = threading.Thread(target=_fake_mpv_error, args=(sock, ready, "property unavailable"))
    th.start()
    assert ready.wait(3)
    ipc = MpvIPC(sock).connect(timeout=5)
    try:
        result = ipc.command("get_property", "nonexistent")
        # Should either raise an exception or have error != "success" in the return
        assert result.get("error") != "success", (
            f"expected error response to be surfaced, got: {result}"
        )
    except (OSError, ValueError, RuntimeError):
        pass  # raising is also acceptable
    finally:
        ipc.close()
        th.join(3)
        if os.path.exists(sock):
            os.unlink(sock)


def test_to_bgra_channel_order_and_premultiply():
    # opaque pixel: just RGBA→BGRA swap
    data, w, h, stride = to_bgra(Image.new("RGBA", (1, 1), (10, 20, 30, 255)))
    assert (w, h, stride) == (1, 1, 4)
    assert tuple(data) == (30, 20, 10, 255)

    # translucent pixel: premultiplied (200,100,50,128) → rgb*(128/255) then swap → (25,50,100,128)
    data2, *_ = to_bgra(Image.new("RGBA", (1, 1), (200, 100, 50, 128)))
    assert tuple(data2) == (25, 50, 100, 128)


# --- Stage 7a: LUT premultiply — byte-identical to the uint16-multiply reference ------------------


def test_premul_lut_exists_and_matches_formula():
    """to_bgra_array premultiplies via a precomputed 256×256 LUT (Stage 7a); the LUT must encode
    exactly value*alpha//255."""
    import numpy as np

    from overlay.mpvio import osd

    lut = osd._PREMUL_LUT
    assert lut.shape == (256, 256) and lut.dtype == np.uint8
    a = np.arange(256, dtype=np.uint16)
    for alpha in (0, 1, 127, 128, 254, 255):
        assert (lut[alpha] == (a * alpha // 255).astype(np.uint8)).all()


def test_to_bgra_array_byte_identical_to_reference():
    """Property test: to_bgra_array must be byte-identical to the original uint16-multiply
    implementation over random RGBA images (all alphas, all channels)."""
    import numpy as np

    from overlay.mpvio.osd import to_bgra_array

    def reference(img):
        arr = np.asarray(img.convert("RGBA"))
        a = arr[:, :, 3:4].astype(np.uint16)
        rgb = (arr[:, :, :3].astype(np.uint16) * a // 255).astype(np.uint8)
        arr = np.dstack([rgb, arr[:, :, 3]])
        return np.ascontiguousarray(arr[:, :, [2, 1, 0, 3]])

    rng = np.random.default_rng(42)
    for _ in range(5):
        raw = rng.integers(0, 256, size=(37, 53, 4), dtype=np.uint8)
        img = Image.fromarray(raw, "RGBA")
        got = to_bgra_array(img)
        want = reference(img)
        assert got.dtype == want.dtype and got.shape == want.shape
        assert (got == want).all()
    # no-premultiply path unchanged too
    img = Image.fromarray(rng.integers(0, 256, size=(8, 8, 4), dtype=np.uint8), "RGBA")
    got = to_bgra_array(img, premultiply=False)
    arr = np.asarray(img)
    assert (got == arr[:, :, [2, 1, 0, 3]]).all()


def _pair():
    import socket as _s

    a, b = _s.socketpair()
    ipc = MpvIPC("unused")
    ipc._sock = a
    ipc._start_reader()  # background reader consumes the injected socket (as connect() would)
    return ipc, b


def test_reader_thread_collects_unsolicited_events():
    """Steady state sends no commands, so the reader thread must collect events off the socket
    independently — otherwise observed properties never update (live-mpv regression). The reader
    replaces the old single-threaded pump(), which was a NO-OP on the Windows pipe."""
    ipc, server = _pair()
    server.sendall(b'{"event":"property-change","id":1,"name":"sub-text","data":"x"}\n')
    import time as _t

    _t.sleep(0.1)  # let the reader thread consume it
    ipc.pump()  # not disconnected → must not raise
    evs = ipc.drain_events()
    assert [e["name"] for e in evs if e.get("event") == "property-change"] == ["sub-text"]
    server.close()
    ipc.close()


def test_reader_delivers_reply_while_buffering_events():
    """A command reply must return even when async events are interleaved on the stream."""
    ipc, server = _pair()
    server.sendall(
        b'{"event":"property-change","id":1,"name":"mouse-pos","data":{"x":1}}\n'
        b'{"data":0.0,"request_id":0,"error":"success"}\n'
    )
    reply = ipc.command("get_property", "time-pos")
    assert reply.get("error") == "success"
    assert any(e.get("name") == "mouse-pos" for e in ipc.drain_events())
    server.close()
    ipc.close()


def test_pump_raises_on_disconnect():
    ipc, server = _pair()
    server.close()  # reader thread sees EOF → sets closed
    import time as _t

    import pytest as _p

    _t.sleep(0.1)
    with _p.raises(OSError):
        ipc.pump()
    ipc.close()
