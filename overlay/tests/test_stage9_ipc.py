"""mpv IPC wire format (overlay-add) + BGRA conversion. No mpv needed and no AF_UNIX server: the
command/reply tests run over a cross-platform ``socket.socketpair()`` wrapped in the Transport port, so
this whole file runs on every OS (the reader-thread/framing invariants themselves live in
``test_transport_contract.py`` — not duplicated here)."""

import json
import socket
import threading
from pathlib import Path

from PIL import Image

from overlay.mpvio.ipc import MpvIPC
from overlay.mpvio.osd import Overlay, to_bgra
from overlay.mpvio.transport import UnixSocketTransport


def _client_with_server():
    """An ``MpvIPC`` on one end of a socketpair with its reader running; returns ``(ipc, server_end)``.
    Cross-platform (Windows emulates socketpair over AF_INET), so no ``skipif`` needed."""
    a, b = socket.socketpair()
    ipc = MpvIPC("unused")
    ipc._transport = UnixSocketTransport(a)
    ipc._start_reader()
    return ipc, b


def _serve_one_command(server: socket.socket, received: list, reply: bytes) -> None:
    """Read one JSON line the client sent (record it), then send ``reply`` — mpv answering a single
    command over the IPC socket."""
    server.settimeout(2.0)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = server.recv(4096)
            if not chunk:
                return
            buf += chunk
    except OSError:
        return
    line, _, _ = buf.partition(b"\n")
    received.append(json.loads(line.decode()))
    server.sendall(reply)


def test_overlay_add_wire_format():
    ipc, server = _client_with_server()
    received: list = []
    th = threading.Thread(
        target=_serve_one_command, args=(server, received, b'{"request_id":0,"error":"success"}\n')
    )
    th.start()

    ov = Overlay(ipc)
    reply = ov.show(Image.new("RGBA", (10, 4), (255, 0, 0, 255)), x=7, y=9, oid=1)
    assert reply.get("error") == "success"
    th.join(3)

    cmd = received[0]["command"]
    assert cmd[0] == "overlay-add"
    assert cmd[1] == 1  # id
    assert cmd[2] == 7 and cmd[3] == 9  # x, y
    assert cmd[5] == 0  # offset
    assert cmd[6] == "bgra"
    assert cmd[7] == 10 and cmd[8] == 4  # w, h
    assert cmd[9] == 40  # stride = w*4
    assert Path(cmd[4]).stat().st_size == 10 * 4 * 4  # BGRA bytes on disk

    ipc.close()
    server.close()


def test_command_raises_on_error_response():
    """``command`` must surface (not swallow) a non-success error reply from mpv."""
    ipc, server = _client_with_server()
    received: list = []
    th = threading.Thread(
        target=_serve_one_command,
        args=(server, received, b'{"request_id":0,"error":"property unavailable"}\n'),
    )
    th.start()
    try:
        result = ipc.command("get_property", "nonexistent")
        assert result.get("error") != "success", f"expected error surfaced, got: {result}"
    finally:
        ipc.close()
        server.close()
        th.join(3)


# --- BGRA conversion (pure) ----------------------------------------------------------------------


def test_to_bgra_channel_order_and_premultiply():
    # opaque pixel: just RGBA→BGRA swap
    data, w, h, stride = to_bgra(Image.new("RGBA", (1, 1), (10, 20, 30, 255)))
    assert (w, h, stride) == (1, 1, 4)
    assert tuple(data) == (30, 20, 10, 255)

    # translucent pixel: premultiplied (200,100,50,128) → rgb*(128/255) then swap → (25,50,100,128)
    data2, *_ = to_bgra(Image.new("RGBA", (1, 1), (200, 100, 50, 128)))
    assert tuple(data2) == (25, 50, 100, 128)


def test_premul_lut_exists_and_matches_formula():
    """to_bgra_array premultiplies via a precomputed 256×256 LUT; the LUT must encode exactly
    value*alpha//255."""
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
