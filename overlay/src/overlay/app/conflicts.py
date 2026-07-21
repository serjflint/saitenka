"""Detect other mpv-overlay tools that would fight over the same OSD / IPC.

Today that's **SubMiner** — the tool this overlay replaces. SubMiner injects its own subtitle overlay
into mpv and binds its own keys, so running it alongside the saitenka plugin draws two overlays over
one video (the user sees flicker / a stuck "overlay loading"). We detect it and step aside — attach
warns and skips rather than doubling up — and doctor flags it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SUBMINER_PROC = "SubMiner.app/Contents/MacOS/SubMiner"


def subminer_installed() -> bool:
    """True if SubMiner is present on this machine (app bundle on macOS, else a CLI on PATH)."""
    if sys.platform == "darwin":
        return Path("/Applications/SubMiner.app").exists()
    return shutil.which("subminer") is not None


def subminer_running() -> bool:
    """True if a SubMiner process is live — it will attach its own overlay to mpv, so we must not."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["pgrep", "-f", _SUBMINER_PROC], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0
    if sys.platform.startswith("win"):
        try:
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq SubMiner.exe"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return "SubMiner.exe" in (r.stdout or "")
    return False
