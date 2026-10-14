"""Stage 16: mpv coexistence — discovery, attach mode, plugin install, id-base namespacing.

All hermetic: discovery is driven through injectable probes (no real filesystem/PATH assumptions),
attach handshakes against a fake in-process IPC server, the lua install/uninstall/backup runs against
a FAKE mpv config home (never the user's real ~/.config/mpv), and the id-base offset is asserted on
the recorded overlay-add commands.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import util

from saitenka.mpvio import discover
from saitenka.mpvio.osd import Overlay

# --- mpv discovery ---------------------------------------------------------------------------


def test_discover_falls_back_to_known_locations(monkeypatch):
    monkeypatch.setattr(discover.shutil, "which", lambda _name: None)
    fake = Path("/Applications/mpv.app/Contents/MacOS/mpv")
    monkeypatch.setattr(discover, "_CANDIDATES", [fake])
    # Patch the seam find_mpv actually uses (_is_exe → Path.is_file), so the test doesn't depend on
    # the fake path really existing on the host — it does on a dev Mac, not on a Linux CI runner.
    monkeypatch.setattr(discover, "_is_exe", lambda p: str(p) == str(fake))
    assert discover.find_mpv() == str(fake)


def test_discover_respects_config_mpv_path(monkeypatch, tmp_path):
    config = tmp_path / "config-mpv"
    env = tmp_path / "env-mpv"
    for path in (config, env):
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    monkeypatch.setenv("SAITENKA_MPV_PATH", str(env))
    monkeypatch.setattr(discover.shutil, "which", lambda _name: "/usr/local/bin/mpv")

    assert discover.find_mpv(config_path=str(config)) == str(config)
    assert discover.find_mpv() == str(env)
    monkeypatch.delenv("SAITENKA_MPV_PATH")
    assert discover.find_mpv() == "/usr/local/bin/mpv"


def test_discover_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(discover.shutil, "which", lambda _name: None)
    monkeypatch.setattr(discover, "_CANDIDATES", [])
    assert discover.find_mpv() is None


# --- attach handshake (shared socket, multiple clients) --------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="AF_UNIX socket sharing is POSIX-only; the Windows named-pipe path is covered by "
    "test_plugin.test_named_pipe_roundtrip",
)
def test_two_clients_share_one_socket():
    """mpv accepts many concurrent IPC clients; our attach must coexist with another (animecards/
    mpv_websocket). Verify two MpvIPC clients handshake against one fake server on the same socket."""
    import shutil
    import tempfile

    from fake_mpv_server import FakeMpvServer

    from saitenka.mpvio.ipc import MpvIPC

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


class _RecIPC(util.FakeIPC):
    pass


def test_overlay_id_base_offsets_overlay_ids():
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


def test_highest_preexisting_overlay_base_stays_in_mpv_range():
    from saitenka.app.overlay_ids import OverlayId

    overlay = Overlay(_RecIPC(), id_base=52)

    assert max(overlay.physical_oid(oid) for oid in OverlayId) == 63


# --- lua plugin install / uninstall / backup ------------------------------------------------


def test_install_plugin_writes_lua_into_fake_scripts_dir(tmp_path):
    from saitenka.app import plugin

    scripts = tmp_path / "mpv" / "scripts"
    dest = plugin.install_plugin(scripts_dir=scripts)
    assert dest.exists() and dest.name == "saitenka.lua"
    lua = dest.read_text()
    # Must spawn the `attach` SUBCOMMAND, not a `--attach` flag (the CLI rejects the flag form).
    assert "'attach'" in lua
    assert "'--attach'" not in lua
    # SAITENKA_BIN is baked to an ABSOLUTE path (bare name wouldn't resolve under a GUI mpv's PATH).
    m = re.search(r"SAITENKA_BIN = \[\[(.*?)\]\]", lua)
    assert m and Path(m.group(1)).is_absolute()  # "/..." on POSIX, "D:\\..." on Windows


def test_install_plugin_backs_up_existing(tmp_path, monkeypatch):
    from saitenka.app import plugin

    scripts = [tmp_path / "mpv" / "scripts", tmp_path / "mpvnet" / "scripts"]
    for directory in scripts:
        directory.mkdir(parents=True)
        (directory / "saitenka.lua").write_text("-- OLD lua\n")
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: scripts)
    dest = plugin.install_plugin()

    assert dest == scripts[0] / "saitenka.lua"
    for directory in scripts:
        backups = list((directory.parent / "saitenka-backups").glob("saitenka.lua.*.bak"))
        assert backups and "OLD" in backups[0].read_text()
        assert not list(directory.glob("*.bak"))
        assert "saitenka" in (directory / "saitenka.lua").read_text()


@pytest.mark.parametrize("present", [(0, 1), (1,)])
def test_uninstall_plugin_backs_up_then_removes(tmp_path, monkeypatch, present):
    from saitenka.app import plugin

    scripts = [tmp_path / "mpv" / "scripts", tmp_path / "mpvnet" / "scripts"]
    for index, directory in enumerate(scripts):
        directory.mkdir(parents=True)
        if index in present:
            (directory / "saitenka.lua").write_text("-- installed\n")
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: scripts)

    backup = plugin.uninstall_plugin()

    assert backup is not None and backup.parent == scripts[min(present)].parent / "saitenka-backups"
    for index, directory in enumerate(scripts):
        assert not (directory / "saitenka.lua").exists()
        backups = list((directory.parent / "saitenka-backups").glob("saitenka.lua.*.bak"))
        assert bool(backups) is (index in present)
        if backups:
            assert "installed" in backups[0].read_text()


def test_uninstall_plugin_noop_when_absent(tmp_path):
    from saitenka.app import plugin

    assert plugin.uninstall_plugin(scripts_dir=tmp_path / "nope") is None
