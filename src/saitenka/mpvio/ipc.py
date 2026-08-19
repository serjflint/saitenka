"""Minimal mpv JSON-IPC client (Unix socket on macOS/Linux, named pipe on Windows).

Transport model (matches every working mpv client — SubMiner's ``net.Socket`` + ``on('data')``,
mpv_websocket's async reader, iwalton3/python-mpv-jsonipc's ``WindowsSocket`` thread): a **background
reader thread** does blocking reads on whichever transport is open and routes each JSON line —
``event`` messages to a thread-safe list, command replies to request-ID-correlated futures. This is
identical on Unix and Windows, so there is no ``select`` (Unix-only) / ``PeekNamedPipe`` (Windows-only)
split: the earlier single-threaded ``pump()`` was a NO-OP on the Windows named pipe, so nothing ever
read it in steady state and hover/mining/quit-detection were all dead even though attach "succeeded".
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka import otel_metrics
from saitenka.mpvio.transport import NamedPipeTransport, Transport, UnixSocketTransport

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from saitenka.runtime import CommandHandled

    class RuntimeGateway(Protocol):
        def close(self) -> None: ...

        def register_observers(self, names: tuple[str, ...]) -> dict[str, dict]: ...

        def publish_legacy_outcome(self, outcome: CommandHandled) -> None: ...

        def submit_mpv(self, **kwargs) -> bool: ...

        def schedule_timer(self, **kwargs) -> bool: ...

        def cancel_timer(self, timer: str) -> bool: ...

        def dispatch_terminal(self, completion) -> None: ...

        def register_job_lane(self, name: str, policy, handler) -> None: ...

        def submit_job(self, **kwargs) -> bool: ...

        def close_job_lane(self, name: str, timeout: float = 2.0) -> bool: ...

    class LegacyEventSource(Protocol):
        def __call__(
            self, timeout: float | None = 0.0, *, ordered_terminals: bool = False
        ) -> list[object]: ...


log = logging.getLogger(__name__)

MPVNET_DEFAULT_PIPE = r"\\.\pipe\mpvsocket"


def normalize_ipc_path(path: str, *, platform: str | None = None) -> str:
    """Accept Windows pipe names copied from Japanese-locale UIs or written as portable slashes."""
    value = str(path).strip().strip('"')
    if (platform or sys.platform) != "win32":
        return value
    value = value.replace("¥", "\\").replace("￥", "\\").replace("/", "\\")
    if "\\" not in value:
        return rf"\\.\pipe\{value}"
    return value


def default_attach_ipc_path() -> str | None:
    """Known player default for a bare attach; Unix has no universal socket name."""
    return MPVNET_DEFAULT_PIPE if sys.platform == "win32" else None


def is_windows_pipe_path(path: str) -> bool:
    return normalize_ipc_path(path).lower().startswith("\\\\.\\pipe\\")


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


# Bounded auto-reconnect budget (per process). mpv.net drops its IPC named pipe mid-session (a
# transient WinError 109 that vanilla mpv doesn't emit); re-dialing the SAME endpoint recovers it
# without the user relaunching. A total cap so a genuinely-gone mpv (quit) still exits after a burst
# of failed dials — no cross-thread reset, so there is no data race on the counter.
_MAX_RECONNECTS = 30

# After a re-dial, probe that mpv actually answers before declaring the reconnect good. A transient
# mpv.net pipe drop (mpv still running) replies in milliseconds; a QUIT — including a self-launched
# run-mode mpv exiting, whose socket can still accept a connect yet never reply — would otherwise hang
# the poll loop for command()'s full timeout, _MAX_RECONNECTS times over. Bail fast instead → exit.
_RECONNECT_PROBE_S = 2.0
_OUTBOUND_MAX = 256


@dataclass(frozen=True, slots=True)
class IPCRequest:
    request_id: int
    connection_epoch: int
    future: Future[dict]
    accepted: bool = True


class MpvIPC:
    """Connect to an mpv ``--input-ipc-server`` and send commands, reading JSON replies.

    Reads run on a daemon reader thread started by :meth:`connect`; replies resolve only the future
    registered for their request ID, so asynchronous cosmetic commands cannot poison later calls."""

    def __init__(self, path: str):
        self.path = normalize_ipc_path(path)
        self._transport: Transport | None = None  # set by connect() (or injected in tests)
        self._buf = b""  # reader-thread-only accumulation buffer
        self._feed_lock = threading.Lock()
        self._bytes_read = (
            0  # total bytes the reader thread got from mpv (0 = never read → pipe dead)
        )
        self._events: list[dict] = []  # async events (property-change, client-message, …)
        #: Signalled whenever an event lands, so a consumer can WAIT for one instead of asking
        #: forty times a second. The reader thread is the only producer; `drain_events` clears it
        #: under the same lock it empties the buffer under, so a wake can never be lost.
        self._event_arrived = threading.Event()
        self._events_lock = threading.Lock()
        self._event_sink: Callable[[dict, int], None] | None = None
        self._connection_sink: Callable[[str, int], None] | None = None
        self._legacy_event_source: LegacyEventSource | None = None
        self._runtime_gateway: RuntimeGateway | None = None
        self._pending: dict[int, tuple[int, Future[dict]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._outbound: queue.Queue[tuple[int, int, bytes, Future[dict]] | None] = queue.Queue(
            maxsize=_OUTBOUND_MAX
        )
        self._next_request_id = 0
        self._connection_epoch = 0
        self._transitioning = threading.Event()
        self._reconnect_lock = threading.Lock()
        self.connected_at: float | None = None
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None
        self._dial: Callable[[str, float], Transport] | None = (
            None  # set by connect(); reconnect reuses
        )
        self._intentional = False  # close() vs a dropped pipe — only the latter reconnects
        self._reconnects_left = _MAX_RECONNECTS
        self._writer = threading.Thread(
            target=self._write_loop,
            name="mpv-ipc-writer",
            daemon=True,
        )
        self._writer.start()

    # --- connection ---------------------------------------------------------------------------
    def connect(self, timeout: float = 10.0, interval: float = 0.1) -> MpvIPC:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        # Windows exposes IPC as a named pipe, Unix as a socket file — identical framing on top (see
        # transport.py). Pick the adapter, then retry-dial until the server is up or the deadline.
        dial = NamedPipeTransport.dial if sys.platform == "win32" else UnixSocketTransport.dial
        self._dial = dial  # reused by the gateway's reconnect actor after a dropped pipe
        while time.monotonic() < deadline:
            try:
                self._transport = dial(self.path, timeout)
                self.connected_at = time.monotonic()
                self._start_reader()
                log.info(
                    "mpv IPC connected via %s at %s",
                    type(self._transport).__name__,
                    self.path,
                )
                return self
            except (OSError, FileNotFoundError) as e:  # server not up yet
                last = e
                time.sleep(interval)
        raise TimeoutError(f"could not connect to mpv IPC at {self.path}: {last}")

    def _start_reader(self) -> None:
        """Spawn the background reader (also called by tests that inject a transport)."""
        transport = self._transport
        assert transport is not None
        closed = self._closed
        epoch = self._connection_epoch
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(transport, closed, epoch),
            name="mpv-ipc-reader",
            daemon=True,
        )
        self._reader.start()

    # --- reader thread ------------------------------------------------------------------------
    def _read_loop(self, transport: Transport, closed: threading.Event, epoch: int) -> None:
        first = True
        try:
            while not closed.is_set():
                chunk = transport.read(65536)
                if not chunk:
                    log.info(
                        "mpv IPC reader: EOF after %d byte(s) — mpv closed the pipe",
                        self._bytes_read,
                    )
                    break  # EOF — mpv quit / pipe closed
                if first:  # decisive Windows diagnostic: did mpv's replies EVER reach us?
                    log.info("mpv IPC reader: first data from mpv (%d byte(s))", len(chunk))
                    first = False
                self._bytes_read += len(chunk)
                self._feed(chunk, connection_epoch=epoch)
        except OSError as e:
            log.warning("mpv IPC reader: read failed (%s) — treating as disconnect", e)
        finally:
            closed.set()
            self._fail_pending({"error": "disconnected"}, epoch=epoch)
            sink = self._connection_sink
            if sink is not None:
                sink("lost", epoch)

    def _feed(self, chunk: bytes, *, connection_epoch: int | None = None) -> None:
        """Accumulate bytes, split complete JSON lines, route events vs replies. Reader-thread only
        (except tests, which drive it directly to exercise parsing without a real transport)."""
        with self._feed_lock:
            if connection_epoch is not None and connection_epoch != self._connection_epoch:
                return
            self._feed_current(chunk, self._connection_epoch)

    def _feed_current(self, chunk: bytes, connection_epoch: int) -> None:
        self._buf += chunk
        while b"\n" in self._buf:
            line, _, self._buf = self._buf.partition(b"\n")
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode())
            except (ValueError, UnicodeDecodeError):
                continue  # never let a garbled line kill the reader
            self._route_message(msg, connection_epoch)

    def _route_message(self, msg: dict, connection_epoch: int) -> None:
        if "event" in msg:
            with self._events_lock:
                sink = self._event_sink
                if sink is not None:
                    sink(dict(msg), connection_epoch)
                    self._event_arrived.set()
                    return
                self._events.append(msg)
                self._event_arrived.set()
            return
        request_id = msg.get("request_id")
        if not isinstance(request_id, int):
            log.debug("mpv IPC: reply without integer request_id dropped")
            return
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            log.debug("mpv IPC: late/unknown reply %d dropped", request_id)
            return
        _epoch, future = pending
        if not future.done():
            future.set_result(msg)

    # --- io -----------------------------------------------------------------------------------
    def _write_target(self, epoch: int) -> tuple[Transport, threading.Event]:
        """Pin one connection generation for a queued write.

        The transport write itself may block, so the lock cannot cover it.  Returning the matching
        close event ensures a late failure from a retired transport cannot poison its replacement.
        """
        with self._write_lock:
            transport = self._transport
            closed = self._closed
            if epoch != self._connection_epoch or closed.is_set() or transport is None:
                raise OSError("mpv IPC disconnected")
            return transport, closed

    def _write_loop(self) -> None:
        """The sole transport writer; callers only perform bounded queue admission."""
        while True:
            try:
                item = self._outbound.get(timeout=0.1)
            except queue.Empty:
                if self._intentional:
                    return
                continue
            if item is None:
                return
            epoch, request_id, data, future = item
            if future.done():
                continue
            closed = None
            try:
                transport, closed = self._write_target(epoch)
                transport.write(data)
            except OSError as error:
                log.warning("mpv IPC: queued write failed (%s) — disconnected", error)
                self._reject_write(request_id, future, "disconnected")
                if closed is not None:
                    closed.set()

    def _reject_write(self, request_id: int, future: Future[dict], error: str) -> None:
        with self._pending_lock:
            self._pending.pop(request_id, None)
        if not future.done():
            future.set_result({"error": error})

    def command_async(
        self,
        *args,
        expected_connection_epoch: int | None = None,
    ) -> IPCRequest:
        """Submit a correlated command without waiting for its reply."""
        future: Future[dict] = Future()
        observed_epoch = self._connection_epoch
        observed_transition = self._transitioning.is_set()
        with self._write_lock:
            with self._pending_lock:
                request_id = self._next_request_id
                self._next_request_id += 1
                epoch = self._connection_epoch
                if self._closed.is_set():
                    future.set_result({"error": "disconnected"})
                    return IPCRequest(request_id, epoch, future, accepted=False)
                required_epoch = (
                    observed_epoch
                    if expected_connection_epoch is None
                    else expected_connection_epoch
                )
                if observed_transition or epoch != required_epoch:
                    future.set_result({"error": "stale-epoch"})
                    return IPCRequest(request_id, epoch, future, accepted=False)
                self._pending[request_id] = (epoch, future)
            payload = json.dumps({"command": list(args), "request_id": request_id}).encode() + b"\n"
            try:
                self._outbound.put_nowait((epoch, request_id, payload, future))
            except queue.Full:
                self._reject_write(request_id, future, "overloaded")
        return IPCRequest(request_id, epoch, future)

    def command(self, *args, timeout: float | None = None) -> dict:
        """Send a correlated command and wait only for its own reply."""
        # Metrics only (timed, not instrumented) — this runs on effectively every poll tick, and a
        # span per call would flood trace.json for no visualization benefit at that frequency.
        with otel_metrics.timed(otel_metrics.ipc_roundtrip_ms):
            cmd = args[0] if args else "?"
            request = self.command_async(*args)
            wait = timeout if timeout is not None else 10.0
            try:
                return request.future.result(timeout=wait)
            except FutureTimeoutError:
                with self._pending_lock:
                    self._pending.pop(request.request_id, None)
                # No reply in `wait`s — on Windows this is the tell-tale of a dead pipe read
                # direction: writes land but mpv's replies/events never come back (bytes_read=0).
                log.warning(
                    "mpv IPC: %r got no reply in %.0fs (bytes from mpv=%d) — replies not reaching us",
                    cmd,
                    wait,
                    self._bytes_read,
                )
                return {"error": "timeout"}

    def _pop_pending(self, epoch: int | None = None) -> list[Future[dict]]:
        with self._pending_lock:
            if epoch is None:
                pending, self._pending = self._pending, {}
            else:
                pending = {
                    request_id: item
                    for request_id, item in self._pending.items()
                    if item[0] == epoch
                }
                self._pending = {
                    request_id: item
                    for request_id, item in self._pending.items()
                    if item[0] != epoch
                }
        return [future for _pending_epoch, future in pending.values()]

    @staticmethod
    def _resolve_pending(futures: list[Future[dict]], result: dict) -> None:
        for future in futures:
            if not future.done():
                future.set_result(dict(result))

    def _fail_pending(self, result: dict, *, epoch: int | None = None) -> None:
        self._resolve_pending(self._pop_pending(epoch), result)

    @property
    def reconnects_left(self) -> int:
        return self._reconnects_left

    @property
    def disconnected(self) -> bool:
        return self._closed.is_set()

    def reconnect_once(self) -> bool:
        """Attempt one epoch-fenced re-dial for the gateway's reconnect actor."""
        with self._reconnect_lock:
            if self._dial is None or self._reconnects_left <= 0 or self._intentional:
                return False
            self._detach_for_reconnect()
            self._reconnects_left -= 1
            transport = self._dial_replacement()
            if transport is None:
                return False
            installed, retired = self._install_replacement(transport)
            if not installed:
                return False
            self._resolve_pending(retired, {"error": "disconnected"})
            if not self._replacement_is_live():
                return False
            log.warning(
                "mpv IPC: reconnected to %s after a dropped pipe (%d reconnect(s) left)",
                self.path,
                self._reconnects_left,
            )
            return True

    @staticmethod
    def _close_transport(transport: Transport) -> None:
        try:
            transport.close()
        except OSError:
            pass

    def _detach_for_reconnect(self) -> None:
        with self._write_lock:
            old_transport, self._transport = self._transport, None
        if old_transport is not None:
            self._close_transport(old_transport)
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.0)

    def _dial_replacement(self) -> Transport | None:
        assert self._dial is not None
        try:
            return self._dial(self.path, 1.0)
        except (OSError, FileNotFoundError) as e:
            log.info("mpv IPC reconnect: %s unavailable (%s) — mpv has quit", self.path, e)
            return None

    def _install_replacement(self, transport: Transport) -> tuple[bool, list[Future[dict]]]:
        self._transitioning.set()
        try:
            with self._write_lock:
                if self._intentional:
                    self._close_transport(transport)
                    return False, []
                with self._feed_lock:
                    self._buf = b""
                    with self._pending_lock:
                        retired_epoch = self._connection_epoch
                        self._connection_epoch += 1
                        retired = [
                            future
                            for pending_epoch, future in self._pending.values()
                            if pending_epoch == retired_epoch
                        ]
                        self._pending = {
                            request_id: item
                            for request_id, item in self._pending.items()
                            if item[0] != retired_epoch
                        }
                with self._events_lock:
                    self._events = []
                self._transport = transport
                self._closed = threading.Event()
                sink = self._connection_sink
                if sink is not None:
                    sink("replaced", self._connection_epoch)
                self._start_reader()
            return True, retired
        finally:
            self._transitioning.clear()

    def _replacement_is_live(self) -> bool:
        # Liveness gate: a re-dial that connects to a QUIT mpv (self-launched run-mode exit, or an
        # external mpv that closed) can accept the socket yet never reply. Probe once — our sentinels
        # ("disconnected"/"timeout") mean gone; ANY real mpv reply (even
        # a property error) proves it's live, so keep the reconnection. Prevents the ~10s×N quit hang.
        if self.command("get_property", "pid", timeout=_RECONNECT_PROBE_S).get("error") in {
            "disconnected",
            "timeout",
        }:
            log.info(
                "mpv IPC reconnect: re-dialed %s but it did not reply — mpv has quit", self.path
            )
            self._closed.set()
            return False
        return True

    def drain_events(
        self, timeout: float | None = 0.0, *, ordered_terminals: bool = False
    ) -> list[object]:
        """Return and clear buffered async events (collected by the reader thread).

        `ordered_terminals` asks the runtime router to return effect completions in envelope
        sequence rather than dispatching them inline; see `LegacyEventRouter.drain_events`.
        """
        if self._legacy_event_source is not None:
            return self._legacy_event_source(timeout, ordered_terminals=ordered_terminals)
        if timeout:
            self._await_event(timeout)
        with self._events_lock:
            buffered, self._events = self._events, []
            self._event_arrived.clear()
            evs: list[object] = list(buffered)
        return evs

    def _await_event(self, timeout: float | None) -> None:
        """Block until an event lands, the connection drops, or the timeout passes.

        Checking the buffer first is what makes this safe against a lost wake: the flag is cleared
        only while holding the lock that empties the buffer, so "flag clear" always means "buffer
        empty" and never "an event arrived and nobody noticed".
        """
        with self._events_lock:
            if self._events:
                return
        if self._closed.is_set():
            return
        self._event_arrived.wait(timeout)

    def install_runtime_ingress(
        self,
        event_sink: Callable[[dict, int], None],
        connection_sink: Callable[[str, int], None],
        legacy_event_source: LegacyEventSource,
        gateway: RuntimeGateway,
    ) -> None:
        """Switch event ownership to a mailbox while the legacy consumer still drives policy."""
        with self._events_lock:
            buffered, self._events = self._events, []
            for event in buffered:
                event_sink(dict(event), self._connection_epoch)
            self._event_sink = event_sink
            self._connection_sink = connection_sink
            self._legacy_event_source = legacy_event_source
            self._runtime_gateway = gateway
        if self._closed.is_set() and not self._intentional:
            connection_sink("lost", self._connection_epoch)

    def publish_legacy_command_outcome(self, outcome: CommandHandled) -> None:
        gateway = self._runtime_gateway
        if gateway is not None:
            gateway.publish_legacy_outcome(outcome)

    def dispatch_runtime_terminal(self, completion) -> None:
        """Run a completion returned by `drain_events(ordered_terminals=True)`."""
        gateway = self._runtime_gateway
        if gateway is not None:
            gateway.dispatch_terminal(completion)

    def submit_runtime_mpv(self, **kwargs) -> bool:
        gateway = self._runtime_gateway
        if gateway is None:
            return False
        return gateway.submit_mpv(**kwargs)

    def schedule_runtime_timer(self, **kwargs) -> bool:
        gateway = self._runtime_gateway
        if gateway is None:
            return False
        return gateway.schedule_timer(**kwargs)

    def cancel_runtime_timer(self, timer: str) -> bool:
        gateway = self._runtime_gateway
        if gateway is None:
            return False
        return gateway.cancel_timer(timer)

    def register_runtime_job_lane(self, name: str, policy, handler) -> bool:
        gateway = self._runtime_gateway
        if gateway is None:
            return False
        gateway.register_job_lane(name, policy, handler)
        return True

    def submit_runtime_job(self, **kwargs) -> bool:
        gateway = self._runtime_gateway
        if gateway is None:
            return False
        return gateway.submit_job(**kwargs)

    def close_runtime_job_lane(self, name: str, timeout: float = 2.0) -> bool:
        gateway = self._runtime_gateway
        if gateway is None:
            return False
        return gateway.close_job_lane(name, timeout)

    def register_runtime_observers(self, names: tuple[str, ...]) -> dict[str, dict]:
        gateway = self._runtime_gateway
        if gateway is None:
            return {}
        return gateway.register_observers(names)

    def close(self) -> None:
        with self._write_lock:
            self._intentional = (
                True  # a real shutdown, not a dropped pipe — never publish a re-dial
            )
            self._closed.set()
            transport, self._transport = self._transport, None
        gateway = self._runtime_gateway
        if gateway is not None:
            gateway.close()
        self._fail_pending({"error": "disconnected"})
        try:
            self._outbound.put_nowait(None)
        except queue.Full:
            # Resolve queued requests first; the daemon writer remains bounded if its transport wedged.
            pass
        if transport is not None:
            try:
                transport.close()  # unblocks the reader thread's blocking read → it exits
            except OSError:
                pass
        # Join the reader so shutdown doesn't race a still-running thread (bounded — the closed
        # transport makes the blocking read return promptly).
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=2.0)
            self._reader = None
        if self._writer.is_alive():
            self._writer.join(timeout=2.0)

    def __enter__(self) -> MpvIPC:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
