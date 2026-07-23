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


def test_dict_db_check_reports_unimported_title(tmp_path, monkeypatch):
    import dicthelp

    present = dicthelp.term_zip(tmp_path / "d1.zip", "Present", [["猫", "ねこ", ["cat"]]])
    dicthelp.db().import_zip(present, imported_at=dicthelp.AT)  # into the per-test hermetic DB
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["Present", "Absent"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    checks = doc.check_dict_db()
    fails = [c for c in checks if c.status == "fail"]
    assert any("Absent" in c.detail for c in fails)
    assert any(c.status == "ok" and "Present" in c.detail for c in checks)


def test_legacy_files_check_ok_when_none(monkeypatch):
    monkeypatch.setattr("overlay.app.paths.legacy_dict_artifacts", lambda: [])
    assert doc.check_legacy_files().status == "ok"


def test_legacy_files_check_warns_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "overlay.app.paths.legacy_dict_artifacts", lambda: [(tmp_path / "dicts", 3, 5_000_000)]
    )
    c = doc.check_legacy_files()
    assert c.status == "warn" and "safe to delete" in c.detail


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


def test_check_tts_reports_availability_with_platform_hint(monkeypatch):
    monkeypatch.setattr("overlay.app.media.tts_available", lambda: True)
    assert doc.check_tts().status == "ok"
    monkeypatch.setattr("overlay.app.media.tts_available", lambda: False)
    monkeypatch.setattr(doc.sys, "platform", "win32")
    c = doc.check_tts()
    assert c.status == "warn" and "language pack" in c.detail  # Windows-specific fix
    monkeypatch.setattr(doc.sys, "platform", "linux")
    assert "espeak" in doc.check_tts().detail


def test_free_threading_check(monkeypatch):
    # On any interpreter the check must classify itself without error.
    c = doc.check_free_threading()
    assert c.status in ("ok", "warn")


def test_python_check_reports_version_and_build():
    import platform

    c = doc.check_python()
    assert c.status == "ok"
    assert platform.python_version() in c.detail  # exact interpreter version is surfaced
    # build fact present so a 3.14 vs 3.14t mix-up is unambiguous in a bug report
    assert ("free-threaded" in c.detail) or ("standard" in c.detail)


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


def test_dict_db_check_no_db_with_config_fails(tmp_path, monkeypatch):
    # config lists dictionaries but the DB was never created → a clear "run import" failure
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["Something"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setattr("overlay.app.dictdb.db_path", lambda: tmp_path / "nope.sqlite")
    checks = doc.check_dict_db()
    assert checks[0].status == "fail" and "import" in checks[0].detail


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
    from overlay.app import jimaku

    monkeypatch.setattr(jimaku, "keychain_get", lambda: None)  # Keychain genuinely empty
    c = doc.check_jimaku()
    assert c.status == "warn" and "GUI-launched" in c.detail


def test_jimaku_env_and_keychain_is_ok(tmp_path, monkeypatch):
    # Key in BOTH $JIMAKU_API_KEY and the Keychain: the resolver reports src=env (env wins), but the
    # Keychain HAS it, so plugin-mode mpv works → doctor must be OK, not a false GUI warning.
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("[jimaku]\nenabled = true\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setenv("JIMAKU_API_KEY", "envkey")
    from overlay.app import jimaku

    monkeypatch.setattr(jimaku, "keychain_get", lambda: "kckey")
    c = doc.check_jimaku()
    assert c.status == "ok" and "Keychain" in c.detail


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


def test_deinflect_installed_is_ok():
    # the [full] test env installs the deinflect add-on → chips enabled
    c = doc.check_deinflect()
    assert c.status == "ok" and "deinflect" in c.detail


def test_deinflect_missing_warns(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "saitenka_deinflect", None)  # → import raises ImportError
    c = doc.check_deinflect()
    assert c.status == "warn" and "deinflect" in c.detail
