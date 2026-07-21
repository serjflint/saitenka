"""Cross-platform path resolution: env overrides, legacy fallback, mpv/mpv.net dir mirroring."""

from __future__ import annotations

import sys

from overlay.app import paths


def test_expand_user_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_TEST_DIR", str(tmp_path))
    assert paths.expand("$SAITENKA_TEST_DIR/x") == tmp_path / "x"
    assert paths.expand("~/y").is_absolute()


def test_pick_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_HOME", str(tmp_path / "custom"))
    assert paths.config_dir() == tmp_path / "custom"


def test_pick_legacy_used_when_it_exists_and_native_does_not(monkeypatch, tmp_path):
    native = tmp_path / "native"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert paths._pick("SAITENKA_UNSET_XYZ", native, legacy) == legacy


def test_pick_native_used_for_fresh_install(monkeypatch, tmp_path):
    native = tmp_path / "native"  # neither exists → idiomatic native
    legacy = tmp_path / "legacy"
    assert paths._pick("SAITENKA_UNSET_XYZ", native, legacy) == native


def test_mpv_config_dir_respects_mpv_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MPV_HOME", str(tmp_path / "mpvhome"))
    assert paths.mpv_config_dir() == tmp_path / "mpvhome"


def test_mpv_config_dir_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("MPV_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.mpv_config_dir() == tmp_path / "Roaming" / "mpv"


def test_mpv_config_dir_posix_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("MPV_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.mpv_config_dir() == tmp_path / "cfg" / "mpv"


def test_mpv_scripts_dirs_includes_mpvnet_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("MPV_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    dirs = paths.mpv_scripts_dirs()
    assert tmp_path / "Roaming" / "mpv" / "scripts" in dirs
    assert tmp_path / "Roaming" / "mpv.net" / "scripts" in dirs


def test_mpv_scripts_dirs_single_on_posix(monkeypatch):
    monkeypatch.delenv("MPV_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert len(paths.mpv_scripts_dirs()) == 1  # no mpv.net off Windows


def test_atomic_write_text_lf_and_creates_parent(tmp_path):
    p = tmp_path / "sub" / "f.txt"
    paths.atomic_write_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"  # LF preserved (no CRLF even on Windows)
    assert list((tmp_path / "sub").glob(".*.tmp")) == []  # temp cleaned up


def test_find_tool_falls_back_to_bin_dirs(monkeypatch, tmp_path):
    import overlay.mpvio.discover as disc

    monkeypatch.setattr(disc.shutil, "which", lambda n: None)
    fake = tmp_path / ("ffmpeg.exe" if disc.os.name == "nt" else "ffmpeg")
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(disc, "_BIN_DIRS", [tmp_path])
    assert disc.find_tool("ffmpeg") == str(fake)


def test_augment_path_prepends_existing_dirs(monkeypatch, tmp_path):
    import overlay.mpvio.discover as disc

    monkeypatch.setattr(disc, "_BIN_DIRS", [tmp_path])
    monkeypatch.setenv("PATH", "/usr/bin")
    disc.augment_path()
    import os

    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path)


def test_sanitize_filename_windows_hazards():
    assert paths.sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert paths.sanitize_filename("name...  ") == "name"  # trailing dots/spaces stripped
    assert paths.sanitize_filename("CON").startswith("_")  # reserved device name prefixed
    assert paths.sanitize_filename("con.txt").startswith("_")  # case-insensitive
    assert paths.sanitize_filename("") == "_"


def test_nfc_normalizes_decomposed():
    import unicodedata

    assert paths.nfc(unicodedata.normalize("NFD", "é")) == unicodedata.normalize("NFC", "é")


def test_long_path_is_noop_on_posix():
    if sys.platform != "win32":
        assert str(paths.long_path("/a/b")) == "/a/b"
