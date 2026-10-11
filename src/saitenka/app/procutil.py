"""Cross-platform subprocess cleanup.

Killing a ``subprocess.Popen`` only kills the direct child: on Windows ``terminate()`` never touches
grandchildren, and on Unix children can be reparented — so a killed overlay could orphan an mpv or
ffmpeg. ``psutil`` walks the whole tree uniformly; a stdlib fallback covers the (unlikely) case where
psutil isn't importable.
"""

from __future__ import annotations

import logging
import subprocess
import time

log = logging.getLogger(__name__)


def run_cancellable(command, *, cancelled=None, timeout=30, **kwargs):
    check = kwargs.pop("check", False)
    if cancelled is None:
        return subprocess.run(command, check=check, timeout=timeout, **kwargs)
    if kwargs.pop("capture_output", False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with subprocess.Popen(command, **kwargs) as proc:
        stdout, stderr = _communicate(proc, command, cancelled, timeout)
        if check and proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, command, stdout, stderr)
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def _communicate(proc, command, cancelled, timeout):
    deadline = time.monotonic() + timeout
    while not cancelled() and time.monotonic() < deadline:
        try:
            return proc.communicate(timeout=max(0.001, min(0.1, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            continue
    kill_process_tree(proc, timeout=0)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
    proc.wait(timeout=1)
    raise subprocess.TimeoutExpired(command, timeout)


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
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except Exception:  # pragma: no cover
            log.debug("process kill fallback failed", exc_info=True)
