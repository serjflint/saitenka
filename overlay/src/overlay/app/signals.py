"""Unified graceful shutdown across POSIX and Windows.

`run`/`attach` already clean up (quit mpv, close the IPC socket, remove temp files) in a ``finally``
that runs when the reader loop exits — Ctrl+C reaches it because SIGINT raises ``KeyboardInterrupt`` by
default. This routes the OTHER termination signals to the same path: POSIX **SIGTERM** (a plain
``kill``) and Windows **SIGBREAK** (Ctrl+Break). Windows has no real SIGTERM delivery, so SIGBREAK is
the graceful one there. Without this, those signals hard-exit the process and skip cleanup, leaving a
stale socket/pipe and temp dirs.
"""

from __future__ import annotations

import logging
import signal

log = logging.getLogger(__name__)


def _raise_keyboard_interrupt(signum, frame):  # pragma: no cover — delivered by the OS
    raise KeyboardInterrupt


def install() -> None:
    """Route termination signals to ``KeyboardInterrupt`` so the existing cleanup runs. Best-effort:
    a no-op off the main thread or where a given signal isn't supported on this platform."""
    for name in ("SIGTERM", "SIGBREAK"):  # SIGINT is already KeyboardInterrupt by default
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise_keyboard_interrupt)
        except (ValueError, OSError, RuntimeError):  # not main thread / unsupported here
            log.debug("could not install handler for %s", name, exc_info=True)
