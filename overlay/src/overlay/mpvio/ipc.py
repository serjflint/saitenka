"""Minimal mpv JSON-IPC client (Unix socket on macOS/Linux, named pipe on Windows).

Transport model (matches every working mpv client — SubMiner's ``net.Socket`` + ``on('data')``,
mpv_websocket's async reader, iwalton3/python-mpv-jsonipc's ``WindowsSocket`` thread): a **background
reader thread** does blocking reads on whichever transport is open and routes each JSON line —
``event`` messages to a thread-safe list, command replies to a single-flight reply channel. This is
identical on Unix and Windows, so there is no ``select`` (Unix-only) / ``PeekNamedPipe`` (Windows-only)
split: the earlier single-threaded ``pump()`` was a NO-OP on the Windows named pipe, so nothing ever
read it in steady state and hover/mining/quit-detection were all dead even though attach "succeeded".
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path

from overlay.mpvio.transport import NamedPipeTransport, Transport, UnixSocketTransport


def default_ipc_path(unique: str) -> str:
    """The ``--input-ipc-server`` value to hand mpv (and connect to) for a self-launched mpv.

    On Windows mpv exposes IPC as a NAMED PIPE, not a filesystem socket — a ``…\\Temp\\…\\mpv.sock``
    path is never connectable (``[Errno 2]``). Return a ``\\\\.\\pipe\\saitenka-<unique>`` name there;
    on Unix return a socket file under the system temp dir. ``unique`` disambiguates concurrent runs
    (e.g. the per-run temp-dir name)."""
    if sys.platform == "win32":
        return rf"\\.\pipe\saitenka-{unique}"
    import tempfile

    return str(Path(tempfile.gettempdir()) / f"saitenka-{unique}.sock")


# Sentinel pushed onto the reply channel when the reader thread sees EOF, so a waiting command()
# unblocks with a disconnect result instead of hanging.
_DISCONNECT = object()


class MpvIPC:
    """Connect to an mpv ``--input-ipc-server`` and send commands, reading JSON replies.

    Reads run on a daemon reader thread started by :meth:`connect`; ``command`` is single-flight
    (called only from the main/IPC thread, as the controller does), so one reply channel suffices."""

    def __init__(self, path: str):
        self.path = path
        self._transport: Transport | None = None  # set by connect() (or injected in tests)
        self._buf = b""  # reader-thread-only accumulation buffer
        self._events: list[dict] = []  # async events (property-change, client-message, …)
        self._events_lock = threading.Lock()
        self._replies: queue.Queue = queue.Queue(maxsize=1)  # single-flight command replies
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None

    # --- connection ---------------------------------------------------------------------------
    def connect(self, timeout: float = 10.0, interval: float = 0.1) -> MpvIPC:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        # Windows exposes IPC as a named pipe, Unix as a socket file — identical framing on top (see
        # transport.py). Pick the adapter, then retry-dial until the server is up or the deadline.
        dial = NamedPipeTransport.dial if sys.platform == "win32" else UnixSocketTransport.dial
        while time.monotonic() < deadline:
            try:
                self._transport = dial(self.path, timeout)
                self._start_reader()
                return self
            except (OSError, FileNotFoundError) as e:  # server not up yet
                last = e
                time.sleep(interval)
        raise TimeoutError(f"could not connect to mpv IPC at {self.path}: {last}")

    def _start_reader(self) -> None:
        """Spawn the background reader (also called by tests that inject a transport)."""
        self._reader = threading.Thread(target=self._read_loop, name="mpv-ipc-reader", daemon=True)
        self._reader.start()

    # --- reader thread ------------------------------------------------------------------------
    def _read_loop(self) -> None:
        try:
            transport = self._transport
            assert transport is not None  # connect()/injection set it before the reader ran
            while not self._closed.is_set():
                chunk = transport.read(65536)
                if not chunk:
                    break  # EOF — mpv quit / pipe closed
                self._feed(chunk)
        except OSError:
            pass  # transport torn down under us → treat as disconnect
        finally:
            self._closed.set()
            try:  # unblock a command() waiting on a reply
                self._replies.put_nowait(_DISCONNECT)
            except queue.Full:
                pass

    def _feed(self, chunk: bytes) -> None:
        """Accumulate bytes, split complete JSON lines, route events vs replies. Reader-thread only
        (except tests, which drive it directly to exercise parsing without a real transport)."""
        self._buf += chunk
        while b"\n" in self._buf:
            line, _, self._buf = self._buf.partition(b"\n")
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode())
            except (ValueError, UnicodeDecodeError):
                continue  # never let a garbled line kill the reader
            if "event" in msg:
                with self._events_lock:
                    self._events.append(msg)
            else:  # a command reply (single-flight, so at most one is awaited)
                try:
                    self._replies.put_nowait(msg)
                except queue.Full:  # a stray/late reply — replace so the newest wins
                    try:
                        self._replies.get_nowait()
                        self._replies.put_nowait(msg)
                    except (queue.Empty, queue.Full):
                        pass

    # --- io -----------------------------------------------------------------------------------
    def _write(self, data: bytes) -> None:
        assert self._transport is not None  # connect()/injection set it
        self._transport.write(data)

    def command(self, *args, timeout: float | None = None) -> dict:
        """Send a command array and return the first non-event reply (or an error dict)."""
        if self._closed.is_set():
            return {"error": "disconnected"}
        # Clear any stale reply left by a previously timed-out command (single-flight otherwise).
        try:
            while True:
                self._replies.get_nowait()
        except queue.Empty:
            pass
        try:
            self._write(json.dumps({"command": list(args)}).encode() + b"\n")
        except OSError:
            self._closed.set()
            return {"error": "disconnected"}
        try:
            msg = self._replies.get(timeout=timeout if timeout is not None else 10.0)
        except queue.Empty:
            return {"error": "timeout"}
        return {"error": "disconnected"} if msg is _DISCONNECT else msg

    def pump(self) -> None:
        """Surface a disconnect so the poll loop stops. The reader thread does the actual socket
        reads now, so steady-state event delivery no longer depends on this being called — but the
        controller's contract (``pump()`` raises ``OSError`` when mpv goes away) is preserved."""
        if self._closed.is_set():
            raise OSError("mpv IPC disconnected")

    def drain_events(self) -> list[dict]:
        """Return and clear buffered async events (collected by the reader thread)."""
        with self._events_lock:
            evs, self._events = self._events, []
        return evs

    def close(self) -> None:
        self._closed.set()
        if self._transport is not None:
            try:
                self._transport.close()  # unblocks the reader thread's blocking read → it exits
            except OSError:
                pass
            self._transport = None
        # Join the reader so shutdown doesn't race a still-running thread (bounded — the closed
        # transport makes the blocking read return promptly).
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=2.0)
            self._reader = None

    def __enter__(self) -> MpvIPC:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
