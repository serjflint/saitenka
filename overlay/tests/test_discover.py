"""mpv discovery — the Windows off-PATH probes (App Paths / default-handler registry) and the
shell-command exe parser. The real registry is behind ``_reg_value`` (monkeypatched here), so these
run on any OS; ``_is_exe`` is exercised against real temp files."""

from __future__ import annotations

from overlay.mpvio import discover as d


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
