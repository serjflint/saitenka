"""A tiny in-process fake of mpv's JSON-IPC unix socket server for attach-mode tests.

mpv accepts multiple concurrent IPC clients on one ``--input-ipc-server`` socket; this fake mirrors
that: it listens on a unix socket, accepts every connection on its own thread, and answers
``get_property`` with a fixed property table (enough to prove two clients can share the socket).
"""

from __future__ import annotations

import json
import socket
import threading


class FakeMpvServer:
    def __init__(self, path: str, props: dict | None = None):
        self.path = path
        self.props = props or {"pause": False, "sub-text": ""}
        self._srv: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    def start(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.path)
        s.listen(8)
        s.settimeout(0.2)
        self._srv = s
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            t.start()
            self._threads.append(t)

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(0.2)
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if not line.strip():
                    continue
                req = json.loads(line.decode())
                cmd = req.get("command", [])
                reply = {"error": "success", "data": None}
                if cmd and cmd[0] == "get_property":
                    reply["data"] = self.props.get(cmd[1])
                conn.sendall(json.dumps(reply).encode() + b"\n")
        conn.close()

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            self._srv.close()
            self._srv = None
