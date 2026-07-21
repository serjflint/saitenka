"""Process-tree termination: a killed overlay must not orphan mpv/ffmpeg."""

from __future__ import annotations

import subprocess
import sys

from overlay.app.procutil import kill_process_tree


def test_kill_process_tree_terminates_running_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert proc.poll() is None
    kill_process_tree(proc, timeout=3)
    proc.wait(timeout=5)
    assert proc.poll() is not None  # dead


def test_kill_process_tree_noop_on_already_exited():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=5)
    kill_process_tree(proc)  # must not raise
