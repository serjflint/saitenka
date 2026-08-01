"""Transport adapters for the mpv JSON-IPC client.

``MpvIPC`` (``ipc.py``) owns the JSON framing + command/event logic; the *byte channel* under it is a
``Transport``. Splitting the two lets the same framing run over a Unix socket (macOS/Linux), a Windows
named pipe, or an in-memory fake — and lets ONE contract suite exercise every adapter
(``tests/test_transport_contract.py``). Each adapter is a blocking byte channel read on ``MpvIPC``'s
background reader thread; ``read`` returns ``b""`` at EOF (the peer closed).
"""

from __future__ import annotations

import socket
import sys
import threading
from typing import Any, Protocol, runtime_checkable


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
    r"""mpv IPC over one overlapped Windows named-pipe handle.

    The reader waits on one OVERLAPPED operation while the controller writes through another. A
    synchronous ``FileIO`` object serializes those directions on Windows, delaying replies/events
    until unrelated input wakes mpv."""

    def __init__(self, handle: int, api: Any) -> None:
        self._handle = handle
        self._api = api
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._read_op: Any | None = None
        self._write_op: Any | None = None
        self._closed = False

    @staticmethod
    def _windows_api() -> Any:
        if sys.platform == "win32":
            import _winapi

            return _winapi
        raise OSError("Windows named pipes are unavailable on this platform")

    @classmethod
    def dial(cls, path: str, _timeout: float) -> NamedPipeTransport:
        api = cls._windows_api()
        handle = api.CreateFile(
            path,
            api.GENERIC_READ | api.GENERIC_WRITE,
            0,
            api.NULL,
            api.OPEN_EXISTING,
            api.FILE_FLAG_OVERLAPPED,
            api.NULL,
        )
        return cls(handle, api)

    def _begin(self, attr: str, operation) -> Any:
        with self._state_lock:
            if self._closed:
                raise OSError("named pipe is closed")
            op, error = operation(self._handle)
            if error not in (0, self._api.ERROR_IO_PENDING):
                op.cancel()
                raise OSError(error, "named-pipe operation failed")
            setattr(self, attr, op)
            return op

    def _finish(self, attr: str, op) -> tuple[int, int]:
        try:
            return op.GetOverlappedResult(True)  # noqa: FBT003  # WinAPI's wait flag
        finally:
            with self._state_lock:
                if getattr(self, attr) is op:
                    setattr(self, attr, None)

    def read(self, n: int) -> bytes:
        op = self._begin("_read_op", lambda handle: self._api.ReadFile(handle, n, overlapped=True))
        _count, error = self._finish("_read_op", op)
        if error in (0, getattr(self._api, "ERROR_MORE_DATA", -1)):
            return bytes(op.getbuffer() or b"")
        if error in (
            self._api.ERROR_BROKEN_PIPE,
            self._api.ERROR_OPERATION_ABORTED,
            getattr(self._api, "ERROR_NO_DATA", -1),
        ):
            return b""
        raise OSError(error, "named-pipe read failed")

    def write(self, data: bytes) -> None:
        sent = 0
        with self._write_lock:
            while sent < len(data):
                chunk = data[sent:]
                op = self._begin(
                    "_write_op",
                    lambda handle, payload=chunk: self._api.WriteFile(
                        handle, payload, overlapped=True
                    ),
                )
                written, error = self._finish("_write_op", op)
                if error != 0:
                    raise OSError(error, "named-pipe write failed")
                if written <= 0:
                    raise OSError("named-pipe write made no progress")
                sent += written

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            handle = self._handle
            pending = (self._read_op, self._write_op)
        for op in pending:
            if op is not None:
                try:
                    op.cancel()
                except OSError:
                    pass
        self._api.CloseHandle(handle)
