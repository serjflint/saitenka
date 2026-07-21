"""Install / uninstall the ``saitenka.lua`` mpv user-script (plugin mode).

Non-destructive: an existing ``saitenka.lua`` is backed up (timestamped) before overwrite, and
uninstall backs up before removing. The default scripts dir is ``~/.config/mpv/scripts/``; tests pass
a fake dir so the user's real mpv config is never touched.
"""

from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

LUA_NAME = "saitenka.lua"

# The `local SAITENKA_BIN = '...'` line the lua declares; install_plugin rewrites its value to the
# resolved absolute path so a GUI-launched mpv (minimal PATH) can still spawn the overlay.
_BIN_LINE_RE = re.compile(r"^local SAITENKA_BIN = .*$", re.MULTILINE)


def resolve_overlay_bin() -> str:
    """Absolute path to the ``saitenka-overlay`` executable, for baking into the plugin.

    A Finder/Dock-launched mpv inherits only launchd's minimal PATH (no ~/.local/bin, no Homebrew
    bin), so the bare command name wouldn't resolve. Prefer PATH lookup; fall back to the console
    script next to the running interpreter; last resort, the bare name."""
    found = shutil.which("saitenka-overlay")
    if found:
        return found
    candidate = Path(sys.executable).with_name("saitenka-overlay")
    if candidate.exists():
        return str(candidate)
    return "saitenka-overlay"


def _bake_bin(text: str, bin_path: str) -> str:
    """Replace the SAITENKA_BIN declaration with the resolved absolute path (lua ``[[...]]`` literal
    so spaces survive).

    The replacement is a FUNCTION, not a string: a Windows path (``C:\\Users\\…``) used as an
    ``re.sub`` replacement string would have its backslash escapes interpreted (``\\U`` → "bad escape
    \\U"), crashing install-plugin/setup. A callable inserts the value verbatim."""
    return _BIN_LINE_RE.sub(lambda _m: f"local SAITENKA_BIN = [[{bin_path}]]", text, count=1)


def _lua_source() -> Path:
    """Path to the bundled ``saitenka.lua``, resolved via importlib.resources."""
    from overlay.assets import lua_path

    return lua_path()


def default_scripts_dir() -> Path:
    """The primary mpv scripts dir, mirroring mpv's own resolution ($MPV_HOME > %APPDATA%\\mpv on
    Windows > ~/.config/mpv). mpv.net's separate dir is added by ``all_scripts_dirs``."""
    from overlay.app.paths import mpv_config_dir

    return mpv_config_dir() / "scripts"


def all_scripts_dirs() -> list[Path]:
    """Every scripts dir to install into so the plugin loads from any launcher: mpv's + mpv.net's."""
    from overlay.app.paths import mpv_scripts_dirs

    return mpv_scripts_dirs()


def _backup(dest: Path) -> Path | None:
    """Back up ``dest`` to a sibling ``saitenka-backups/`` dir OUTSIDE the scripts folder — mpv loads
    every file directly under ``scripts/``, so a ``.bak`` left there is picked up as a broken script."""
    if not dest.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = dest.parent.parent / "saitenka-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{dest.name}.{ts}.bak"
    backup.write_bytes(dest.read_bytes())
    return backup


def install_plugin(scripts_dir: Path | None = None) -> Path:
    """Copy ``saitenka.lua`` into the mpv scripts dir(s), backing up any existing copy first. With no
    explicit ``scripts_dir`` it installs into EVERY dir from ``all_scripts_dirs`` (mpv + mpv.net on
    Windows) so the plugin loads whichever player the user launches. Returns the primary path."""
    targets = [scripts_dir] if scripts_dir is not None else all_scripts_dirs()
    from overlay.app.paths import atomic_write_text

    lua = _bake_bin(_lua_source().read_text(encoding="utf-8"), resolve_overlay_bin())
    installed: list[Path] = []
    for d in targets:
        d.mkdir(parents=True, exist_ok=True)
        dest = d / LUA_NAME
        _backup(dest)
        # newline="\n": keep the Lua LF-only even on Windows (text-mode write would emit CRLF).
        atomic_write_text(dest, lua, newline="\n")
        installed.append(dest)
    return installed[0]


def uninstall_plugin(scripts_dir: Path | None = None) -> Path | None:
    """Remove ``saitenka.lua`` from the mpv scripts dir(s), backing each up first. Returns the first
    backup path, or None if nothing was installed anywhere."""
    targets = [scripts_dir] if scripts_dir is not None else all_scripts_dirs()
    first_backup: Path | None = None
    for d in targets:
        dest = d / LUA_NAME
        if not dest.exists():
            continue
        backup = _backup(dest)
        dest.unlink()
        if first_backup is None:
            first_backup = backup
    return first_backup
