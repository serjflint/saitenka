"""mpv binary auto-discovery — used by launch mode, doctor, and the wizard.

Order: an explicit ``config_path`` (from ``overlay.toml``'s ``mpv_path``) → ``PATH`` → known install
locations (``/Applications/mpv.app``, Homebrew prefixes, scoop/choco/winget shims). Returns the first
existing executable, or None so the caller can print an install hint.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Known install locations by platform, probed in order after PATH. Kept as a module list so tests
# can inject a deterministic candidate set.
_CANDIDATES: list[Path] = [
    # macOS app bundle + Homebrew (Apple Silicon / Intel)
    Path("/Applications/mpv.app/Contents/MacOS/mpv"),
    Path("/opt/homebrew/bin/mpv"),
    Path("/usr/local/bin/mpv"),
    Path("/usr/bin/mpv"),
    # Windows package-manager shims (scoop / choco / winget)
    Path.home() / "scoop" / "shims" / "mpv.exe",
    Path("C:/ProgramData/chocolatey/bin/mpv.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "mpv.exe",
]


def _is_exe(p: Path) -> bool:
    return os.path.isfile(p) and os.access(p, os.X_OK)


def find_mpv(config_path: str | None = None) -> str | None:
    """Resolve an mpv executable, or None if none is found."""
    if config_path:
        p = Path(config_path).expanduser()
        if _is_exe(p):
            return str(p)
    on_path = shutil.which("mpv")
    if on_path:
        return on_path
    for cand in _CANDIDATES:
        if str(cand) and _is_exe(cand):
            return str(cand)
    return None
