"""Cross-platform path resolution — the single source of truth for every directory we touch.

Three layers, following the best-practice split (platformdirs docs; mpv manual "Files"):

1. **Our own dirs** (config / data / cache) → platform-native via ``platformdirs`` (``%LOCALAPPDATA%``
   on Windows, ``~/Library/Application Support`` on macOS, ``$XDG_*`` on Linux), overridable by env,
   with a **legacy ``~/.config``-style fallback on POSIX only** so existing macOS/Linux installs never
   move (no cache rebuild, no stranded config). Windows always uses the native dir — ``~/.config`` was
   never idiomatic there.
2. **mpv / mpv.net dirs** (scripts, mpv.conf) → MIRROR mpv's OWN resolution (``$MPV_HOME`` on all OSes
   > ``portable_config`` next to the exe on Windows > ``%APPDATA%\\mpv`` on Windows / ``~/.config/mpv``
   on macOS+Linux). Never impose our convention on another app's dirs.
3. Every path is user-overridable (env here; ``overlay.toml`` keys at the call sites) and always
   ``expanduser()`` + ``expandvars()``-expanded.

Precedence at each call site: explicit CLI flag > ``overlay.toml`` value > env var > platform default.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

import platformdirs

APP = "saitenka"

# Filename hardening for Windows (invalid chars, reserved device names).
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def nfc(s: str) -> str:
    """Unicode NFC-normalise a string (macOS stores paths NFD; normalise for stable comparison)."""
    return unicodedata.normalize("NFC", s)


def expand(p: str | os.PathLike) -> Path:
    """``~`` and ``$VAR``/``%VAR%`` expansion + NFC-normalisation for a user-supplied path."""
    return Path(nfc(os.path.expandvars(str(Path(p).expanduser()))))


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """A filename safe on Windows too: strip invalid chars (``<>:"/\\|?*`` + control), trailing dots/
    spaces, and reserved device names (``CON``/``PRN``/``NUL``/``COM1``…). Prefixed names like
    ``saitenka-CON`` are already safe; this is defence-in-depth for content-derived names."""
    n = _INVALID_FILENAME.sub(replacement, name).rstrip(" .")
    if not n:
        return replacement
    if n.split(".")[0].upper() in _WIN_RESERVED:
        n = f"{replacement}{n}"
    return n


def long_path(p: str | os.PathLike) -> Path:
    """On Windows, prefix a long (≥~260 char) ABSOLUTE path with ``\\\\?\\`` so it isn't rejected at
    MAX_PATH. No-op on other OSes and for short paths."""
    p = Path(p)
    s = str(p)
    if os.name == "nt" and len(s) >= 240 and not s.startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(p.resolve()))
    return p


def atomic_write_text(
    path: str | os.PathLike, text: str, *, encoding: str = "utf-8", newline: str = "\n"
) -> Path:
    """Durably write text: a temp file in the SAME dir → ``fsync`` → ``os.replace`` (atomic on POSIX
    and on NTFS). A plain ``write_text`` is not atomic — a crash / power loss mid-write leaves a
    truncated config or Lua script. ``newline="\\n"`` also keeps generated Lua/TOML LF-only on Windows
    (text-mode writes would translate to CRLF, which some consumers mishandle)."""
    p = long_path(path)  # Windows MAX_PATH safety for deep cache/config paths
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def _pick(env_var: str, native: Path, legacy: Path) -> Path:
    """env override > (legacy if it already exists, else native). The legacy check keeps existing
    installs in place; a fresh install (neither exists) gets the idiomatic native dir.

    The legacy roots (``~/.config``, ``~/.local/share``, ``~/.cache``) are XDG — a POSIX-only
    convention. On Windows they were NEVER idiomatic, so we skip the legacy branch there and always
    use the platform-native ``%LOCALAPPDATA%\\saitenka`` dir (matching where the dict cache lives);
    otherwise a stray ``C:\\Users\\…\\.config\\saitenka`` from an earlier build would keep winning and
    the config would drift away from the data/cache dirs."""
    override = os.environ.get(env_var)
    if override:
        return expand(override)
    if sys.platform != "win32" and legacy.exists() and not native.exists():
        return legacy
    return native


# --- our own dirs -------------------------------------------------------------------------------


def config_dir() -> Path:
    return _pick(
        "SAITENKA_HOME",
        Path(platformdirs.user_config_dir(APP, appauthor=False)),
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP,
    )


def data_dir() -> Path:
    return _pick(
        "SAITENKA_DATA_DIR",
        Path(platformdirs.user_data_dir(APP, appauthor=False)),
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP,
    )


def cache_dir() -> Path:
    # Legacy cache root was ~/.cache/saitenka-overlay (holds dicts/ + overlay.log) — match it exactly
    # so the expensive SQLite dict cache is never rebuilt for existing users.
    return _pick(
        "SAITENKA_CACHE_DIR",
        Path(platformdirs.user_cache_dir(APP, appauthor=False)),
        Path.home() / ".cache" / "saitenka-overlay",
    )


# --- mpv / mpv.net dirs (mirror mpv's own resolution) ------------------------------------------


def mpv_config_dir() -> Path:
    """mpv's config dir: ``$MPV_HOME`` (all OSes) > ``%APPDATA%\\mpv`` (Windows) / ``~/.config/mpv``
    (macOS, Linux honoring ``$XDG_CONFIG_HOME``). ``portable_config`` next to the exe is handled by
    the caller when the mpv binary is known."""
    home = os.environ.get("MPV_HOME")
    if home:
        return expand(home)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "mpv"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mpv"


def mpvnet_config_dir() -> Path | None:
    """mpv.net is a SEPARATE app on Windows with its own ``%APPDATA%\\mpv.net`` tree (the binary is
    ``mpvnet.exe``). None off Windows / when ``%APPDATA%`` is unset."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "mpv.net"
    return None


def mpv_scripts_dirs() -> list[Path]:
    """Every scripts dir a user-script should be installed into so it loads regardless of which mpv
    the user launches: mpv's own ``scripts/`` plus mpv.net's when that app is present (Windows)."""
    dirs = [mpv_config_dir() / "scripts"]
    net = mpvnet_config_dir()
    if net is not None:
        dirs.append(net / "scripts")
    return dirs


def mpv_conf_paths() -> list[Path]:
    """Candidate ``mpv.conf`` locations to inspect (doctor): mpv's own, plus mpv.net's on Windows."""
    paths = [mpv_config_dir() / "mpv.conf"]
    net = mpvnet_config_dir()
    if net is not None:
        paths.append(net / "mpv.conf")
    return paths
