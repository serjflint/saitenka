"""Transport adapters for the mpv JSON-IPC client.

``MpvIPC`` (``ipc.py``) owns the JSON framing + command/event logic; the *byte channel* under it is a
``Transport``. Splitting the two lets the same framing run over a Unix socket (macOS/Linux), a Windows
named pipe, or an in-memory fake — and lets ONE contract suite exercise every adapter
(``tests/test_transport_contract.py``). Each adapter is a blocking byte channel read on ``MpvIPC``'s
background reader thread; ``read`` returns ``b""`` at EOF (the peer closed).
"""

from __future__ import annotations

import socket
from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    """A connected, blocking byte channel to mpv's IPC endpoint."""

    def read(self, n: int) -> bytes:
        """Block for up to ``n`` bytes; return ``b""`` on EOF."""
        ...

    def write(self, data: bytes) -> None:
        """Send ``data`` in full."""
        ...

    def close(self) -> None:
        """Close the channel — unblocks a ``read`` pending on the reader thread."""
        ...


class UnixSocketTransport:
    """mpv IPC over an ``AF_UNIX`` stream socket (macOS/Linux)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    @classmethod
    def dial(cls, path: str, timeout: float) -> UnixSocketTransport:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(path)
        s.settimeout(None)  # blocking reads on the reader thread
        return cls(s)

    def read(self, n: int) -> bytes:
        return self._sock.recv(n)

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        self._sock.close()


class NamedPipeTransport:
    r"""mpv IPC over a Windows named pipe (``\\.\pipe\…``), opened as a raw unbuffered file. A background
    reader thread does blocking ``read``s — the single-threaded ``pump()`` this replaced was a NO-OP on
    the pipe, which is why hover/mining/quit-detection were dead on Windows even though attach
    'succeeded'."""

    def __init__(self, pipe: BinaryIO) -> None:
        self._pipe = pipe

    @classmethod
    def dial(cls, path: str, timeout: float) -> NamedPipeTransport:
        # timeout unused (open() doesn't block-dial like connect()); kept for a uniform dial() signature.
        return cls(open(path, "r+b", buffering=0))

    def read(self, n: int) -> bytes:
        return self._pipe.read(n) or b""

    def write(self, data: bytes) -> None:
        self._pipe.write(data)
        self._pipe.flush()

    def close(self) -> None:
        self._pipe.close()
