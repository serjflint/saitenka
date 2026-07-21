"""Stage 16: mpv coexistence — discovery, attach mode, plugin install, id-base namespacing.

All hermetic: discovery is driven through injectable probes (no real filesystem/PATH assumptions),
attach handshakes against a fake in-process IPC server, the lua install/uninstall/backup runs against
a FAKE mpv config home (never the user's real ~/.config/mpv), and the id-base offset is asserted on
the recorded overlay-add commands.
"""

from __future__ import annotations

import re
from pathlib import Path


from overlay.mpvio import discover
from overlay.mpvio.osd import Overlay


# --- mpv discovery ---------------------------------------------------------------------------


def test_discover_prefers_path(monkeypatch):
    monkeypatch.setattr(discover.shutil, "which", lambda name: "/usr/local/bin/mpv")
    assert discover.find_mpv() == "/usr/local/bin/mpv"


def test_discover_falls_back_to_known_locations(monkeypatch):
    monkeypatch.setattr(discover.shutil, "which", lambda name: None)
    fake = Path("/Applications/mpv.app/Contents/MacOS/mpv")
    monkeypatch.setattr(discover, "_CANDIDATES", [fake])
    monkeypatch.setattr(discover.os.path, "isfile", lambda p: str(p) == str(fake))
    monkeypatch.setattr(discover.os, "access", lambda p, mode: True)
    assert discover.find_mpv() == str(fake)


def test_discover_respects_config_mpv_path(monkeypatch, tmp_path):
    mpv = tmp_path / "mympv"
    mpv.write_text("#!/bin/sh\n")
    mpv.chmod(0o755)
    monkeypatch.setattr(discover.shutil, "which", lambda name: None)
    assert discover.find_mpv(config_path=str(mpv)) == str(mpv)


def test_discover_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(discover.shutil, "which", lambda name: None)
    monkeypatch.setattr(discover, "_CANDIDATES", [])
    assert discover.find_mpv() is None


# --- attach handshake (shared socket, multiple clients) --------------------------------------


def test_two_clients_share_one_socket():
    """mpv accepts many concurrent IPC clients; our attach must coexist with another (animecards/
    mpv_websocket). Verify two MpvIPC clients handshake against one fake server on the same socket."""
    import shutil
    import tempfile

    from fake_mpv_server import FakeMpvServer

    from overlay.mpvio.ipc import MpvIPC

    # short base dir: AF_UNIX paths are capped ~104 chars on macOS (pytest's tmp_path is too long)
    base = tempfile.mkdtemp(prefix="sk-")
    sock = str(Path(base) / "m.sock")
    server = FakeMpvServer(sock)
    server.start()
    try:
        a = MpvIPC(sock).connect(timeout=5)
        b = MpvIPC(sock).connect(timeout=5)  # second client on the SAME socket
        assert a.command("get_property", "pause")["data"] is False
        assert b.command("get_property", "pause")["data"] is False
        a.close()
        b.close()
    finally:
        server.stop()
        shutil.rmtree(base, ignore_errors=True)


# --- id-base namespacing ---------------------------------------------------------------------


class _RecIPC:
    def __init__(self):
        self.commands: list = []

    def command(self, *args, **kw):
        self.commands.append(args)
        return {"data": None}


def test_overlay_id_base_offsets_overlay_ids(tmp_path, monkeypatch):
    """With id_base=10, an overlay op for logical oid 2 must physically address 11 (10 + 2 - 1),
    so a coexisting script owning 1..6 is not clobbered. Default base 1 = no offset (unchanged)."""
    import numpy as np

    bgra = np.zeros((2, 2, 4), dtype=np.uint8)

    default = Overlay(_RecIPC())
    default.show_bgra(bgra, oid=2)
    assert default.ipc.commands[0][1] == 2  # unchanged by default

    based = Overlay(_RecIPC(), id_base=10)
    based.show_bgra(bgra, oid=2)
    based.hide(oid=2)
    assert based.ipc.commands[0][1] == 11  # overlay-add addressed 10 + (2-1)
    assert based.ipc.commands[1] == ("overlay-remove", 11)


# --- lua plugin install / uninstall / backup ------------------------------------------------


def test_install_plugin_writes_lua_into_fake_scripts_dir(tmp_path):
    from overlay.app import plugin

    scripts = tmp_path / "mpv" / "scripts"
    dest = plugin.install_plugin(scripts_dir=scripts)
    assert dest.exists() and dest.name == "saitenka.lua"
    lua = dest.read_text()
    # Must spawn the `attach` SUBCOMMAND, not a `--attach` flag (the CLI rejects the flag form).
    assert "'attach'" in lua
    assert "'--attach'" not in lua
    # SAITENKA_BIN is baked to an ABSOLUTE path (bare name wouldn't resolve under a GUI mpv's PATH).
    m = re.search(r"SAITENKA_BIN = \[\[(.*?)\]\]", lua)
    assert m and m.group(1).startswith("/")


def test_install_plugin_backs_up_existing(tmp_path):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    existing = scripts / "saitenka.lua"
    existing.write_text("-- OLD lua\n")
    dest = plugin.install_plugin(scripts_dir=scripts)
    # backups live OUTSIDE scripts/ (mpv would try to load a .bak left in scripts/ as a script)
    backups = list((scripts.parent / "saitenka-backups").glob("saitenka.lua.*.bak"))
    assert backups and "OLD" in backups[0].read_text()
    assert not list(scripts.glob("*.bak"))  # nothing mpv-loadable left behind
    assert "saitenka-overlay" in dest.read_text()


def test_uninstall_plugin_backs_up_then_removes(tmp_path):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "saitenka.lua").write_text("-- installed\n")
    backup = plugin.uninstall_plugin(scripts_dir=scripts)
    assert not (scripts / "saitenka.lua").exists()
    assert backup is not None and "installed" in backup.read_text()


def test_uninstall_plugin_noop_when_absent(tmp_path):
    from overlay.app import plugin

    assert plugin.uninstall_plugin(scripts_dir=tmp_path / "nope") is None
