#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# ///
"""A minimal fake mpv for the launch/spawn contract (SubMiner's fake-binary pattern).

Parses ``--input-ipc-server=<unix socket path>`` out of its argv, listens there, records its full argv
to ``--fake-log=<path>``, accepts one client, pushes an unsolicited ``property-change`` event the way a
real mpv would, then stays up until it's killed. Lets a test exercise the REAL subprocess + IPC-socket
handshake that ``cli.run`` performs — without a real mpv, a display, or ffmpeg. POSIX only (AF_UNIX);
the Windows named-pipe variant is the real-Windows-executor's job (R5)."""

from __future__ import annotations

import json
import socket
import sys
import time


def main() -> int:
    argv = sys.argv[1:]

    def _opt(prefix: str) -> str | None:
        return next((a[len(prefix) :] for a in argv if a.startswith(prefix)), None)

    log = _opt("--fake-log=")
    if log:
        with open(log, "w", encoding="utf-8") as f:
            json.dump(argv, f)

    sock_path = _opt("--input-ipc-server=")
    if not sock_path:
        return 2

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(10)
    conn, _ = srv.accept()
    conn.sendall(b'{"event":"property-change","name":"sub-text","data":"fake"}\n')
    while True:  # serve until the parent terminates us
        time.sleep(0.1)


if __name__ == "__main__":
    sys.exit(main() or 0)
