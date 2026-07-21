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
    so spaces survive)."""
    return _BIN_LINE_RE.sub(f"local SAITENKA_BIN = [[{bin_path}]]", text, count=1)


def _lua_source() -> Path:
    """Path to the bundled ``saitenka.lua``, resolved via importlib.resources."""
    from overlay.assets import lua_path

    return lua_path()


def default_scripts_dir() -> Path:
    import os

    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "mpv" / "scripts"


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
    """Copy ``saitenka.lua`` into ``scripts_dir`` (default ~/.config/mpv/scripts), backing up any
    existing copy first. Returns the installed path."""
    scripts_dir = scripts_dir or default_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dest = scripts_dir / LUA_NAME
    _backup(dest)
    lua = _bake_bin(_lua_source().read_text(encoding="utf-8"), resolve_overlay_bin())
    dest.write_text(lua, encoding="utf-8")
    return dest


def uninstall_plugin(scripts_dir: Path | None = None) -> Path | None:
    """Remove ``saitenka.lua`` from ``scripts_dir``, backing it up first. Returns the backup path,
    or None if nothing was installed."""
    scripts_dir = scripts_dir or default_scripts_dir()
    dest = scripts_dir / LUA_NAME
    if not dest.exists():
        return None
    backup = _backup(dest)
    dest.unlink()
    return backup
