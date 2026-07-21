"""Minimal mpv JSON-IPC client (Unix socket on macOS/Linux, named pipe on Windows)."""

from __future__ import annotations

import json
import select
import socket
import sys
import time


class MpvIPC:
    """Connect to an mpv ``--input-ipc-server`` and send commands, reading JSON replies."""

    def __init__(self, path: str):
        self.path = path
        self._sock: socket.socket | None = None
        self._pipe: object | None = None  # Windows file handle (BufferedRandom)
        self._buf = b""
        self.events: list[dict] = []  # async events (client-message, …) seen while reading replies

    # --- connection ---------------------------------------------------------------------------
    def connect(self, timeout: float = 10.0, interval: float = 0.1) -> MpvIPC:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if sys.platform == "win32":
                    self._pipe = open(self.path, "r+b", buffering=0)  # noqa: SIM115
                else:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(timeout)
                    s.connect(self.path)
                    self._sock = s
                return self
            except (OSError, FileNotFoundError) as e:  # server not up yet
                last = e
                time.sleep(interval)
        raise TimeoutError(f"could not connect to mpv IPC at {self.path}: {last}")

    # --- io -----------------------------------------------------------------------------------
    def _write(self, data: bytes) -> None:
        if self._sock is not None:
            self._sock.sendall(data)
        else:
            assert self._pipe is not None  # connect() set exactly one transport
            self._pipe.write(data)  # type: ignore[attr-defined]
            self._pipe.flush()  # type: ignore[attr-defined]

    def _read_line(self) -> bytes:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536) if self._sock is not None else self._pipe.read(65536)  # type: ignore[union-attr]
            if not chunk:
                break
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def command(self, *args, timeout: float | None = None) -> dict:
        """Send a command array and return the first non-event reply."""
        payload = json.dumps({"command": list(args)}).encode() + b"\n"
        self._write(payload)
        while True:
            line = self._read_line()
            if not line.strip():
                if not line:
                    return {"error": "disconnected"}
                continue
            msg = json.loads(line.decode())
            if "event" in msg:  # buffer async events; keep waiting for the command reply
                self.events.append(msg)
                continue
            return msg

    def pump(self) -> None:
        """Read everything currently available on the socket WITHOUT blocking and buffer the
        events. The steady-state loop sends no commands (properties arrive via observe_property),
        so without an explicit pump nothing ever reads the socket: events rot in the kernel buffer
        and an mpv quit (EOF) goes unnoticed. Raises OSError on disconnect."""
        if self._sock is None:
            return  # Windows named pipe: command() reads still collect events; no non-blocking peek
        while True:
            readable, _, _ = select.select([self._sock], [], [], 0)
            if not readable:
                return
            chunk = self._sock.recv(65536)
            if not chunk:
                raise OSError("mpv IPC disconnected")
            self._buf += chunk
            while b"\n" in self._buf:
                line, _, self._buf = self._buf.partition(b"\n")
                if not line.strip():
                    continue
                msg = json.loads(line.decode())
                if "event" in msg:
                    self.events.append(msg)
                # a non-event line here would be an orphaned command reply — command() is
                # synchronous and single-flight, so this can't happen; drop rather than crash

    def drain_events(self) -> list[dict]:
        """Return and clear buffered async events (collected by pump() and command() reads)."""
        evs, self.events = self.events, []
        return evs

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._pipe is not None:
            self._pipe.close()  # type: ignore[attr-defined]
            self._pipe = None

    def __enter__(self) -> MpvIPC:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
