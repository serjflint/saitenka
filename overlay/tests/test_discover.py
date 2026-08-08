"""mpv discovery — the Windows off-PATH probes (App Paths / default-handler registry) and the
shell-command exe parser. The real registry is behind ``_reg_value`` (monkeypatched here), so these
run on any OS; ``_is_exe`` is exercised against real temp files."""

from __future__ import annotations

import os

import pytest
from overlay.mpvio import discover as d


def _script(path, exit_code: int) -> str:
    """Write an executable POSIX script that exits with *exit_code*, return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def _clear_probe_cache():
    d._runs_ok.cache_clear()
    yield
    d._runs_ok.cache_clear()


def test_exe_from_command_quoted_and_bare():
    assert (
        d._exe_from_command(r'"C:\Program Files\mpv.net\mpvnet.exe" "%1"')
        == r"C:\Program Files\mpv.net\mpvnet.exe"
    )
    assert d._exe_from_command(r"C:\mpv\mpv.exe %1") == r"C:\mpv\mpv.exe"
    assert d._exe_from_command("") is None
    assert d._exe_from_command(None) is None


def test_registry_app_paths_finds_off_path_mpv(tmp_path, monkeypatch):
    exe = tmp_path / "mpv.exe"
    exe.write_text("")
    exe.chmod(0o755)

    # Only the HKCU App Paths entry for mpv.exe resolves; everything else is a miss.
    def _reg(root, subkey, _name=""):
        if root == "HKCU" and subkey.endswith(r"App Paths\mpv.exe"):
            return f'"{exe}"'  # installers often store the path quoted
        return None

    monkeypatch.setattr(d, "_reg_value", _reg)
    assert d._windows_registry_mpv() == str(exe)


def test_registry_default_handler_only_trusts_known_mpv_binary(tmp_path, monkeypatch):
    exe = tmp_path / "mpvnet.exe"
    exe.write_text("")
    exe.chmod(0o755)

    def _reg(_root, subkey, _name=""):
        if "App Paths" in subkey:
            return None  # nothing under App Paths → fall through to the default handler
        if subkey.endswith(r"FileExts\.mkv\UserChoice"):
            return "MyMpvNet"
        if subkey == r"MyMpvNet\shell\open\command":
            return f'"{exe}" "%1"'
        return None

    monkeypatch.setattr(d, "_reg_value", _reg)
    assert d._windows_registry_mpv() == str(exe)


def test_registry_default_handler_rejects_non_mpv_player(monkeypatch):
    def _reg(_root, subkey, _name=""):
        if "App Paths" in subkey:
            return None
        if subkey.endswith(r"FileExts\.mkv\UserChoice"):
            return "VLC.mkv"
        if subkey == r"VLC.mkv\shell\open\command":
            return r'"C:\Program Files\VideoLAN\VLC\vlc.exe" --started-from-file "%1"'
        return None

    monkeypatch.setattr(d, "_reg_value", _reg)
    assert d._windows_registry_mpv() is None  # VLC is not mpv → not accepted


def test_registry_probe_is_noop_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(d, "_reg_value", lambda *_a, **_k: None)
    assert d._windows_registry_mpv() is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX exec-probe scripts")
@pytest.mark.usefixtures("_clear_probe_cache")
class TestFindToolHealthProbe:
    """find_tool skips a present-but-broken binary that shadows a working one — the live MacPorts
    ffprobe SIGABRT that blocked every sub-resync (dangling dylib → aborts, but which() still found it)."""

    def test_broken_path_hit_falls_through_to_a_healthy_bin_dir_copy(self, tmp_path, monkeypatch):
        broken = _script(tmp_path / "path" / "ffprobe", 1)  # on PATH, aborts on exec
        healthy_dir = tmp_path / "brew"
        healthy = _script(healthy_dir / "ffprobe", 0)  # in a bin dir, runs
        monkeypatch.setattr(d.shutil, "which", lambda _n: broken)
        monkeypatch.setattr(d, "_BIN_DIRS", [healthy_dir])
        assert d.find_tool("ffprobe") == healthy

    def test_lone_candidate_is_returned_unprobed(self, tmp_path, monkeypatch):
        """A single (even broken) candidate is returned as-is — probing can't conjure a working copy,
        and the single-install common case must not pay a spawn or change behaviour."""
        only = _script(tmp_path / "ffprobe", 1)
        monkeypatch.setattr(d.shutil, "which", lambda _n: only)
        monkeypatch.setattr(d, "_BIN_DIRS", [])
        assert d.find_tool("ffprobe") == only

    def test_tool_without_a_version_flag_is_not_probed(self, tmp_path, monkeypatch):
        """Only ffmpeg/ffprobe have a registered probe flag; an unknown tool keeps first-hit-wins even
        with multiple candidates (no probe flag ⇒ we can't distinguish broken from a bad-flag exit)."""
        path_hit = _script(tmp_path / "path" / "alass", 1)
        other_dir = tmp_path / "other"
        _script(other_dir / "alass", 0)
        monkeypatch.setattr(d.shutil, "which", lambda _n: path_hit)
        monkeypatch.setattr(d, "_BIN_DIRS", [other_dir])
        assert d.find_tool("alass") == path_hit  # PATH hit wins, unprobed

    def test_find_healthy_tool_flags_a_lone_broken_binary(self, tmp_path, monkeypatch):
        """The doctor case: a lone (unprobed by find_tool) ffprobe that aborts on exec resolves to a
        path but healthy=False — so doctor flags the #100 SIGABRT that find_tool alone returns as ok."""
        broken = _script(tmp_path / "ffprobe", 1)
        monkeypatch.setattr(d.shutil, "which", lambda _n: broken)
        monkeypatch.setattr(d, "_BIN_DIRS", [])
        assert d.find_healthy_tool("ffprobe") == (broken, False)

    def test_find_healthy_tool_confirms_a_working_binary(self, tmp_path, monkeypatch):
        healthy = _script(tmp_path / "ffprobe", 0)
        monkeypatch.setattr(d.shutil, "which", lambda _n: healthy)
        monkeypatch.setattr(d, "_BIN_DIRS", [])
        assert d.find_healthy_tool("ffprobe") == (healthy, True)

    def test_find_healthy_tool_trusts_a_found_tool_with_no_probe_flag(self, tmp_path, monkeypatch):
        """No registered probe flag (alass) ⇒ found is trusted (can't distinguish broken from a
        bad-flag exit), so healthy mirrors presence."""
        alass = _script(
            tmp_path / "alass", 1
        )  # would fail a probe, but there's no flag to probe with
        monkeypatch.setattr(d.shutil, "which", lambda _n: alass)
        monkeypatch.setattr(d, "_BIN_DIRS", [])
        assert d.find_healthy_tool("alass") == (alass, True)

    def test_find_healthy_tool_reports_a_missing_tool(self, monkeypatch):
        monkeypatch.setattr(d.shutil, "which", lambda _n: None)
        monkeypatch.setattr(d, "_BIN_DIRS", [])
        assert d.find_healthy_tool("ffprobe") == (None, False)
