"""Cross-platform subprocess cleanup.

Killing a ``subprocess.Popen`` only kills the direct child: on Windows ``terminate()`` never touches
grandchildren, and on Unix children can be reparented — so a killed overlay could orphan an mpv or
ffmpeg. ``psutil`` walks the whole tree uniformly; a stdlib fallback covers the (unlikely) case where
psutil isn't importable.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def kill_process_tree(proc, timeout: float = 5.0) -> None:
    """Terminate ``proc`` AND all descendants: terminate → wait ``timeout`` → hard-kill survivors."""
    if proc is None or proc.poll() is not None:  # already exited
        return
    try:
        import psutil
    except ImportError:  # pragma: no cover — psutil is a declared dep
        _fallback(proc, timeout)
        return
    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    tree = [*parent.children(recursive=True), parent]
    for p in tree:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(tree, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def _fallback(proc, timeout: float) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:  # pragma: no cover
            log.debug("process kill fallback failed", exc_info=True)
