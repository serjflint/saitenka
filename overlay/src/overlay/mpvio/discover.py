"""mpv binary auto-discovery — used by launch mode, doctor, and the wizard.

Order: an explicit ``config_path`` (from ``overlay.toml``'s ``mpv_path``) → ``PATH`` → known install
locations (``/Applications/mpv.app``, Homebrew prefixes, scoop/choco/winget shims). Returns the first
existing executable, or None so the caller can print an install hint.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Known install locations by platform, probed in order after PATH. Kept as a module list so tests
# can inject a deterministic candidate set. On Windows mpv is frequently NOT on PATH (winget's
# mpv.net installs `mpvnet.exe`, shinchiro/MPV-Player land under Program Files) — so we probe the
# common install dirs AND accept mpv.net's `mpvnet.exe`, which drives IPC/attach fine.
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
_CANDIDATES: list[Path] = [
    # macOS app bundle + Homebrew (Apple Silicon / Intel)
    Path("/Applications/mpv.app/Contents/MacOS/mpv"),
    Path("/opt/homebrew/bin/mpv"),
    Path("/usr/local/bin/mpv"),
    Path("/usr/bin/mpv"),
    # Windows package-manager shims (scoop / choco / winget)
    Path.home() / "scoop" / "shims" / "mpv.exe",
    Path("C:/ProgramData/chocolatey/bin/mpv.exe"),
    _LOCALAPPDATA / "Microsoft" / "WinGet" / "Links" / "mpv.exe",
    # Windows common install dirs (vanilla mpv)
    Path("C:/Program Files/mpv/mpv.exe"),
    Path("C:/Program Files/MPV Player/mpv.exe"),
    Path("C:/mpv/mpv.exe"),
    # mpv.net (winget id `mpv.net`; binary is mpvnet.exe / mpvnet.com — NOT `mpv`)
    _LOCALAPPDATA / "Programs" / "mpv.net" / "mpvnet.exe",
    Path("C:/Program Files/mpv.net/mpvnet.exe"),
]

# Env override so a GUI-launched / off-PATH mpv can be pinned without editing the config (parity with
# SubMiner's SUBMINER_MPV_PATH). Checked before PATH/candidates, after an explicit config path.
_MPV_ENV = "SAITENKA_MPV_PATH"

_MPV_EXE_NAMES = ("mpv.exe", "mpvnet.exe", "mpvnet.com")  # accepted Windows binaries


def _reg_value(root: str, subkey: str, name: str = "") -> str | None:
    """Read a registry string value (Windows only; ``None`` elsewhere or on any miss). Isolated so
    tests monkeypatch THIS instead of the real registry — the winreg import stays lazy + Windows-only.
    The positive ``sys.platform == 'win32'`` guard is deliberate: mypy skips the winreg block on other
    platforms (no stubs) without a `warn_unreachable` complaint."""
    if sys.platform == "win32":
        import winreg

        hive = {
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKCR": winreg.HKEY_CLASSES_ROOT,
        }[root]
        try:
            with winreg.OpenKey(hive, subkey) as k:
                val, _ = winreg.QueryValueEx(k, name)
        except OSError:
            return None
        return str(val) if val else None
    return None


def _exe_from_command(cmd: str | None) -> str | None:
    """Pull the executable out of a registry ``shell\\open\\command`` string like ``"C:\\x\\mpv.exe" "%1"``
    (quoted path) or ``C:\\x\\mpv.exe %1`` (bare). Pure — unit-tested."""
    if not cmd or not (cmd := cmd.strip()):
        return None
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 1 else None
    return cmd.split(" %", 1)[0].split()[0] or None  # bare: up to the "%1" arg → first token


def _registry_app_paths_mpv() -> str | None:
    """App Paths registry entries — installers register the full exe path there."""
    for root in ("HKCU", "HKLM"):
        for base in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        ):
            for exe_name in ("mpv.exe", "mpvnet.exe"):
                val = _reg_value(root, rf"{base}\{exe_name}")
                if val and _is_exe(p := Path(val.strip('"'))):
                    return str(p)
    return None


def _registry_default_handler_mpv() -> str | None:
    """The default video-file handler — only trusted when it resolves to a known mpv binary (the
    default app is often VLC/PotPlayer/Store Films&TV)."""
    for ext in (".mkv", ".mp4", ".webm"):
        progid = _reg_value(
            "HKCU",
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice",
            "ProgId",
        )
        if not progid:
            continue
        exe = _exe_from_command(_reg_value("HKCR", rf"{progid}\shell\open\command"))
        if exe and Path(exe).name.lower() in _MPV_EXE_NAMES and _is_exe(Path(exe)):
            return exe
    return None


def _windows_registry_mpv() -> str | None:
    """Find mpv/mpv.net via the registry when it's installed but off-PATH: App Paths first, then the
    default video-file handler."""
    return _registry_app_paths_mpv() or _registry_default_handler_mpv()


def _is_exe(p: Path) -> bool:
    # os.access(X_OK) is unreliable for .exe on Windows (it doesn't model the exec bit); an existing
    # regular file is enough there.
    return Path(p).is_file() and (os.name == "nt" or os.access(p, os.X_OK))


# Standard bin dirs a GUI-launched process (Finder/Dock/Explorer) does NOT get on PATH, but where the
# tools we shell out to (ffmpeg/ffprobe, alass/ffsubsync) actually live.
_BIN_DIRS: list[Path] = [
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path("/usr/bin"),
    Path.home() / ".local" / "bin",
    _LOCALAPPDATA / "Microsoft" / "WinGet" / "Links",
    Path("C:/ffmpeg/bin"),
]


def find_tool(name: str) -> str | None:
    """Resolve a helper binary (ffmpeg/ffprobe/…): PATH, then the standard bin dirs above — so mining
    works even from a GUI-launched (plugin-mode) mpv whose minimal PATH lacks Homebrew / ~/.local/bin."""
    on_path = shutil.which(name)
    if on_path:
        return on_path
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _BIN_DIRS:
        cand = d / exe
        if _is_exe(cand):
            return str(cand)
    return None


def augment_path() -> None:
    """Prepend the standard bin dirs to ``$PATH`` (idempotent) so bare-name subprocesses resolve under
    a GUI launch. Call once at startup. Only existing dirs not already present are added."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    add = [str(d) for d in _BIN_DIRS if d.is_dir() and str(d) not in parts]
    if add:
        os.environ["PATH"] = os.pathsep.join([*add, *parts])


def find_mpv(config_path: str | None = None) -> str | None:
    """Resolve an mpv (or mpv.net) executable, or None if none is found.

    Order: explicit ``config_path`` (``overlay.toml`` ``mpv_path``) → ``$SAITENKA_MPV_PATH`` → PATH
    (``mpv`` then ``mpvnet``) → Windows registry (App Paths / default handler) → known install
    locations."""
    if config_path:
        p = Path(config_path).expanduser()
        if _is_exe(p):
            return str(p)
    env = os.environ.get(_MPV_ENV)
    if env:
        p = Path(env).expanduser()
        if _is_exe(p):
            return str(p)
    for name in ("mpv", "mpvnet"):
        on_path = shutil.which(name)
        if on_path:
            return on_path
    reg = (
        _windows_registry_mpv()
    )  # off-PATH but installed (App Paths / default handler) — Windows only
    if reg:
        return reg
    for cand in _CANDIDATES:
        if str(cand) and _is_exe(cand):
            return str(cand)
    return None
