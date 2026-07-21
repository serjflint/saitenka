"""Stage 14: `saitenka-overlay doctor` health check + `init` first-run wizard.

Doctor is a set of pure, individually-mockable checks returning ``Check(name, status, detail)``
(status ✓ ok / ! warn / ✗ fail) plus a printer and a ``--json`` mode. It WARNS, never modifies.
The wizard proposes a config and writes ``~/.config/saitenka/overlay.toml`` only on confirm, backing
up an existing file first (timestamped — non-destructive rule). Everything is hermetic: fake homes,
mocked subprocess/urllib, no network, no touching the user's real files.
"""

from __future__ import annotations

import tomllib


from overlay.app import doctor as doc
from overlay.app import init_wizard as wiz


# --- individual checks -----------------------------------------------------------------------


def _patch_find_mpv(monkeypatch, result):
    # check_mpv resolves via find_mpv (config → env → PATH → known dirs / mpv.net), so patch that,
    # not shutil.which — otherwise the host's real Homebrew mpv leaks in via the candidate list.
    import overlay.mpvio.discover as disc

    monkeypatch.setattr(disc, "find_mpv", lambda *a, **k: result)


def test_mpv_check_pass(monkeypatch):
    monkeypatch.setattr(doc, "_run", lambda *a, **k: "mpv 0.38.0\n")
    _patch_find_mpv(monkeypatch, "/usr/bin/mpv")
    c = doc.check_mpv()
    assert c.status == "ok"
    assert "0.38" in c.detail


def test_mpv_check_too_old(monkeypatch):
    monkeypatch.setattr(doc, "_run", lambda *a, **k: "mpv 0.35.0\n")
    _patch_find_mpv(monkeypatch, "/usr/bin/mpv")
    c = doc.check_mpv()
    assert c.status == "fail"
    assert "0.37" in c.detail  # explains the minimum for overlay-add BGRA


def test_mpv_check_missing(monkeypatch):
    _patch_find_mpv(monkeypatch, None)
    c = doc.check_mpv()
    assert c.status == "fail"
    assert "mpv" in c.detail.lower()


def test_mpv_check_mpvnet_unparseable_version(monkeypatch):
    # mpv.net's --version string doesn't match the `mpv vX.Y` regex; treat a responding binary as
    # present (warn), not missing.
    monkeypatch.setattr(doc, "_run", lambda *a, **k: "mpv.net v7.1.2.0\n")
    _patch_find_mpv(monkeypatch, r"C:\\Users\\x\\mpv.net\\mpvnet.exe")
    c = doc.check_mpv()
    assert c.status == "warn"
    assert "mpv.net" in c.detail


def test_ffmpeg_check_needs_aac(monkeypatch):
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        doc, "_run", lambda *a, **k: " A....D libmp3lame  MP3\n V....D libx264  H.264\n"
    )
    c = doc.check_ffmpeg()
    assert c.status == "warn"  # no aac encoder → mining audio won't encode
    assert "aac" in c.detail


def test_ffmpeg_check_ok(monkeypatch):
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(doc, "_run", lambda *a, **k: " A....D aac  AAC (Advanced Audio Coding)\n")
    c = doc.check_ffmpeg()
    assert c.status == "ok"


def test_config_check_parses(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["a.zip"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    c = doc.check_config()
    assert c.status == "ok"


def test_config_check_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SAITENKA_CONFIG", str(tmp_path / "nope.toml"))
    c = doc.check_config()
    assert c.status == "warn"  # no config yet → run `init`


def test_dict_zips_check_reports_missing(tmp_path, monkeypatch):
    present = tmp_path / "d1.zip"
    present.write_bytes(b"PK\x03\x04")
    cfg = tmp_path / "overlay.toml"
    cfg.write_text(f'dicts = ["{present}", "{tmp_path / "missing.zip"}"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    checks = doc.check_dict_files()
    fails = [c for c in checks if c.status == "fail"]
    assert any("missing.zip" in c.detail for c in fails)
    assert any(c.status == "ok" and "d1.zip" in c.detail for c in checks)


def test_anki_check_reachable(monkeypatch):
    monkeypatch.setattr(doc, "_anki_call", lambda action, **kw: 6 if action == "version" else [])
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status in ("ok", "warn")


def test_anki_check_unreachable(monkeypatch):
    def boom(action, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(doc, "_anki_call", boom)
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "warn"  # optional dep — warn, not fail
    assert "AnkiConnect" in c.detail


def test_free_threading_check(monkeypatch):
    # On any interpreter the check must classify itself without error.
    c = doc.check_free_threading()
    assert c.status in ("ok", "warn")


def test_mpv_ipc_coexistence_reports_known_sockets(tmp_path, monkeypatch):
    mpvconf = tmp_path / "mpv.conf"
    mpvconf.write_text("input-ipc-server=/tmp/mpv-socket\n")
    monkeypatch.setattr(doc, "_mpv_conf_path", lambda: mpvconf)
    c = doc.check_mpv_ipc()
    assert c.status in ("ok", "warn")
    assert "/tmp/mpv-socket" in c.detail  # animecards socket recognised


def test_plugin_not_installed_is_ok(tmp_path, monkeypatch):
    from overlay.app import plugin

    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [tmp_path / "scripts"])
    c = doc.check_plugin()
    assert c.status == "ok" and "not installed" in c.detail


def test_plugin_broken_attach_flag_fails(tmp_path, monkeypatch):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "saitenka.lua").write_text("args = { 'saitenka-overlay', '--attach', sock }\n")
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [scripts])
    c = doc.check_plugin()
    assert c.status == "fail" and "install-plugin" in c.detail


def test_plugin_installed_with_baked_path_is_ok(tmp_path, monkeypatch):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    # a real install bakes the absolute overlay-bin path → doctor sees it as correct
    bin_path = tmp_path / "bin" / "saitenka-overlay"
    bin_path.parent.mkdir()
    bin_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(plugin, "resolve_overlay_bin", lambda: str(bin_path))
    plugin.install_plugin(scripts_dir=scripts)
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [scripts])
    c = doc.check_plugin()
    assert c.status == "ok" and str(bin_path) in c.detail


def test_plugin_bare_bin_fails(tmp_path, monkeypatch):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # correct `attach` form but a BARE bin name — a Finder-launched mpv can't resolve it
    (scripts / "saitenka.lua").write_text(
        "local SAITENKA_BIN = 'saitenka-overlay'\nargs = { SAITENKA_BIN, 'attach', sock }\n"
    )
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [scripts])
    c = doc.check_plugin()
    assert c.status == "fail" and "bare" in c.detail


def test_plugin_baked_path_gone_warns(tmp_path, monkeypatch):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "saitenka.lua").write_text(
        "local SAITENKA_BIN = [[/nope/saitenka-overlay]]\nargs = { SAITENKA_BIN, 'attach', sock }\n"
    )
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [scripts])
    c = doc.check_plugin()
    assert c.status == "warn" and "no longer exists" in c.detail


def test_sub_auto_all_warns(tmp_path, monkeypatch):
    mpvconf = tmp_path / "mpv.conf"
    mpvconf.write_text("sub-auto=all\n")
    monkeypatch.setattr(doc, "_mpv_conf_path", lambda: mpvconf)
    c = doc.check_sub_auto()
    assert c.status == "warn" and "sub-auto=all" in c.detail


def test_sub_auto_fuzzy_is_ok(tmp_path, monkeypatch):
    mpvconf = tmp_path / "mpv.conf"
    mpvconf.write_text("sub-auto=fuzzy\n")
    monkeypatch.setattr(doc, "_mpv_conf_path", lambda: mpvconf)
    c = doc.check_sub_auto()
    assert c.status == "ok" and "fuzzy" in c.detail


def test_dict_location_warns_on_protected(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["~/Documents/y/a.zip"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr(doc, "is_protected", lambda p: "Documents" in str(p))
    c = doc.check_dict_locations()
    assert c.status == "warn" and "copy-dicts" in c.detail


def test_dict_location_ok_when_outside(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["~/.local/share/saitenka/dicts/a.zip"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr(doc, "is_protected", lambda p: False)
    c = doc.check_dict_locations()
    assert c.status == "ok"


def test_jimaku_disabled_is_ok(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[jimaku]\nenabled = false\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    c = doc.check_jimaku()
    assert c.status == "ok" and "disabled" in c.detail


def test_jimaku_enabled_without_key_warns(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[jimaku]\nenabled = true\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    from overlay.app import jimaku

    monkeypatch.setattr(jimaku, "keychain_get", lambda: None)
    c = doc.check_jimaku()
    assert c.status == "warn" and "set-jimaku-key" in c.detail


def test_jimaku_env_only_warns_about_gui(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[jimaku]\nenabled = true\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setenv("JIMAKU_API_KEY", "envkey")
    c = doc.check_jimaku()
    assert c.status == "warn" and "GUI-launched" in c.detail


def test_jimaku_keychain_key_is_ok(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[jimaku]\nenabled = true\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.delenv("JIMAKU_API_KEY", raising=False)
    from overlay.app import jimaku

    monkeypatch.setattr(jimaku, "keychain_get", lambda: "kckey")
    c = doc.check_jimaku()
    assert c.status == "ok" and "keychain" in c.detail


def test_recent_errors_tails_log(tmp_path, monkeypatch):
    logf = tmp_path / "overlay.log"
    logf.write_text("2026-07-21 A\n2026-07-21 B ERROR boom\n")
    monkeypatch.setattr(doc, "LOG_PATH", logf)
    c = doc.check_recent_errors()
    assert "boom" in c.detail


def test_run_all_checks_and_json(monkeypatch):
    # Force every check into a known shape via a stub list; ensure summary + json serialise.
    fake = [doc.Check("mpv", "ok", "ok"), doc.Check("anki", "warn", "meh")]
    report = doc.Report(fake)
    assert report.exit_code == 0  # warns don't fail
    j = report.to_json()
    assert j["checks"][0]["name"] == "mpv"
    assert j["summary"]["ok"] == 1 and j["summary"]["warn"] == 1


def test_report_fails_on_any_fail():
    report = doc.Report([doc.Check("mpv", "fail", "missing")])
    assert report.exit_code == 1


# --- init wizard -----------------------------------------------------------------------------


def test_wizard_writes_config_on_confirm(tmp_path, monkeypatch):
    dest = tmp_path / "saitenka" / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(dest))
    proposal = {"slang": "ja,jpn,jp", "dicts": ["/x/a.zip"]}
    wiz.write_config(proposal, confirm=lambda _prompt: True)
    assert dest.exists()
    data = tomllib.loads(dest.read_text())
    assert data["slang"] == "ja,jpn,jp"
    assert data["dicts"] == ["/x/a.zip"]


def test_wizard_declined_writes_nothing(tmp_path, monkeypatch):
    dest = tmp_path / "overlay.toml"
    monkeypatch.setenv("SAITENKA_CONFIG", str(dest))
    wiz.write_config({"slang": "ja"}, confirm=lambda _prompt: False)
    assert not dest.exists()


def test_wizard_backs_up_existing_config(tmp_path, monkeypatch):
    dest = tmp_path / "overlay.toml"
    dest.write_text('slang = "OLD"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(dest))
    backup = wiz.write_config({"slang": "NEW"}, confirm=lambda _prompt: True)
    assert backup is not None and backup.exists()
    assert 'slang = "OLD"' in backup.read_text()  # timestamped backup preserved the old file
    assert tomllib.loads(dest.read_text())["slang"] == "NEW"


def test_dict_check_flags_bare_title_specifically(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["JMdict [2026-06-27]"]\n')  # a bare Yomitan TITLE, not a file path
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    fails = [c for c in doc.check_dict_files() if c.status == "fail"]
    assert fails and "looks like a Yomitan title" in fails[0].detail
    assert "import-yomitan" in fails[0].detail
