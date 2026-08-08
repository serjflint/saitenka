"""Stage 14: `saitenka doctor` health check + `init` first-run wizard.

Doctor is a set of pure, individually-mockable checks returning ``Check(name, status, detail)``
(status ✓ ok / ! warn / ✗ fail) plus a printer and a ``--json`` mode. It WARNS, never modifies.
The wizard proposes a config and writes ``~/.config/saitenka/overlay.toml`` only on confirm, backing
up an existing file first (timestamped — non-destructive rule). Everything is hermetic: fake homes,
mocked subprocess/urllib, no network, no touching the user's real files.
"""

from __future__ import annotations

import json
import tomllib

from overlay.app import doctor as doc
from overlay.app import init_wizard as wiz

# --- individual checks -----------------------------------------------------------------------


def _patch_find_mpv(monkeypatch, result):
    # check_mpv resolves via find_mpv (config → env → PATH → known dirs / mpv.net), so patch that,
    # not shutil.which — otherwise the host's real Homebrew mpv leaks in via the candidate list.
    import overlay.mpvio.discover as disc

    monkeypatch.setattr(disc, "find_mpv", lambda *_a, **_k: result)


def test_mpv_check_pass(monkeypatch):
    monkeypatch.setattr(doc, "_run", lambda *_a, **_k: "mpv 0.38.0\n")
    _patch_find_mpv(monkeypatch, "/usr/bin/mpv")
    c = doc.check_mpv()
    assert c.status == "ok"
    assert "0.38" in c.detail


def test_mpv_check_too_old(monkeypatch):
    monkeypatch.setattr(doc, "_run", lambda *_a, **_k: "mpv 0.35.0\n")
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
    monkeypatch.setattr(doc, "_run", lambda *_a, **_k: "mpv.net v7.1.2.0\n")
    _patch_find_mpv(monkeypatch, r"C:\\Users\\x\\mpv.net\\mpvnet.exe")
    c = doc.check_mpv()
    assert c.status == "warn"
    assert "mpv.net" in c.detail


def test_mpv_check_mpvnet_parseable_version_is_labeled(monkeypatch):
    # The friend's case: mpv.net reports a parseable "mpv 0.41" — label it mpv.net so the report names
    # which player is active (its IPC/track quirks differ from vanilla mpv).
    monkeypatch.setattr(doc, "_run", lambda *_a, **_k: "mpv 0.41.0\n")
    _patch_find_mpv(monkeypatch, r"C:\\Users\\x\\Programs\\mpv.net\\mpvnet.exe")
    c = doc.check_mpv()
    assert c.status == "ok"
    assert "mpv.net" in c.detail
    assert "0.41" in c.detail


def test_ffmpeg_check_needs_aac(monkeypatch):
    monkeypatch.setattr(doc.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        doc, "_run", lambda *_a, **_k: " A....D libmp3lame  MP3\n V....D libx264  H.264\n"
    )
    c = doc.check_ffmpeg()
    assert c.status == "warn"  # no aac encoder → mining audio won't encode
    assert "aac" in c.detail


def test_ffmpeg_check_ok(monkeypatch):
    monkeypatch.setattr(doc.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(doc, "_run", lambda *_a, **_k: " A....D aac  AAC (Advanced Audio Coding)\n")
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


def test_config_check_reports_invalid_windows_pipe_escape(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text(r'mpv_socket = "\\.\pipe\mpvsocket"' + "\n")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))

    c = doc.check_config()

    assert c.status == "fail"
    assert "single-quoted TOML" in c.detail


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


def test_dict_db_default_view_is_a_counts_line_not_a_wall(tmp_path, monkeypatch):
    # The itemised per-title list is hidden (info); the one visible ok line is the per-kind counts.
    import dicthelp

    for title in ("Alpha", "Beta"):
        z = dicthelp.term_zip(tmp_path / f"{title}.zip", title, [["猫", "ねこ", ["cat"]]])
        dicthelp.db().import_zip(z, imported_at=dicthelp.AT)
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["Alpha", "Beta"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    checks = doc.check_dict_db()
    visible = [c for c in checks if not c.info]
    assert [c.detail for c in visible] == [
        "dicts: 2 · freq: 0 · pitch: 0"
    ]  # one line, not two rows
    assert all(
        c.info for c in checks if c.detail.startswith("dicts: Alpha") or "imported in" in c.detail
    )


def test_legacy_files_check_ok_when_none(monkeypatch):
    monkeypatch.setattr("overlay.app.paths.legacy_dict_artifacts", list)
    assert doc.check_legacy_files().status == "ok"


def test_legacy_files_check_warns_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "overlay.app.paths.legacy_dict_artifacts", lambda: [(tmp_path / "dicts", 3, 5_000_000)]
    )
    c = doc.check_legacy_files()
    assert c.status == "warn" and "safe to delete" in c.detail


def test_anki_check_reachable(monkeypatch):
    replies = {"version": 6, "deckNames": ["Saitenka::Mining"], "modelNames": ["Lapis"]}
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "ok"


def test_anki_check_missing_note_type_fails(monkeypatch):
    # the note type can't be auto-created, so a configured-but-absent one is an error, not a warning
    replies = {"version": 6, "deckNames": ["Saitenka::Mining"], "modelNames": ["Basic"]}
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "fail"
    assert "Lapis" in c.detail


def test_anki_check_missing_deck_only_warns(monkeypatch):
    # a deck IS auto-created on the first mine, so its absence is a heads-up, not an error
    replies = {"version": 6, "deckNames": [], "modelNames": ["Lapis"]}
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "warn"


def test_anki_check_flags_missing_mining_field(monkeypatch):
    # the note type exists but lacks a mapped field → warn (not fail): mining would write nothing there
    replies = {
        "version": 6,
        "deckNames": ["Saitenka::Mining"],
        "modelNames": ["Lapis"],
        "modelFieldNames": ["Expression", "ExpressionReading"],  # missing Sentence/Glossary/…
    }
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    monkeypatch.setattr(doc, "load_config", dict)  # default (full Lapis) field map
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "warn"
    assert "Sentence" in c.detail  # a missing field is named


def test_anki_check_ok_when_all_mining_fields_present(monkeypatch):
    from overlay.app.anki import LAPIS_FIELDS

    replies = {
        "version": 6,
        "deckNames": ["Saitenka::Mining"],
        "modelNames": ["Lapis"],
        "modelFieldNames": list(LAPIS_FIELDS.values()),  # every mapped field exists
    }
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    monkeypatch.setattr(doc, "load_config", dict)
    assert doc.check_anki(deck="Saitenka::Mining", model="Lapis").status == "ok"


def test_anki_check_flags_unknown_card_format_marker(monkeypatch):
    # #192: a {marker} Saitenka can't fill would render an empty field → warn (no Anki call needed)
    replies = {"version": 6, "deckNames": ["Saitenka::Mining"], "modelNames": ["Lapis"]}
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    monkeypatch.setattr(doc, "load_config", lambda: {"mine": {"card_format": {"Word": "{bogus}"}}})
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "warn" and "bogus" in c.detail


def test_anki_check_flags_card_format_field_absent_from_note_type(monkeypatch):
    # card_format's KEYS are the note fields (it wins wholesale) — a key the note type lacks → warn
    replies = {
        "version": 6,
        "deckNames": ["Saitenka::Mining"],
        "modelNames": ["Lapis"],
        "modelFieldNames": ["Expression"],  # note type has no "Ghost" field
    }
    monkeypatch.setattr(doc, "_anki_call", lambda action, **_kw: replies.get(action, []))
    monkeypatch.setattr(
        doc, "load_config", lambda: {"mine": {"card_format": {"Ghost": "{expression}"}}}
    )
    c = doc.check_anki(deck="Saitenka::Mining", model="Lapis")
    assert c.status == "warn" and "Ghost" in c.detail


def _patch_known(monkeypatch, cfg, anki):
    monkeypatch.setattr(doc, "load_config", lambda: cfg)
    monkeypatch.setattr(doc, "_anki_call", anki)


def test_known_check_ok_when_unconfigured(monkeypatch):
    _patch_known(monkeypatch, {}, lambda *_a, **_k: [])
    assert doc.check_known().status == "ok"


def test_known_check_fails_when_deck_missing(monkeypatch):
    _patch_known(monkeypatch, {"known": {"Ghost": ["Front"]}}, lambda *_a, **_k: ["Real"])
    c = doc.check_known()
    assert c.status == "fail"
    assert "Ghost" in c.detail


def test_known_check_fails_when_field_missing(monkeypatch):
    def anki(action, **_kw):
        return {
            "deckNames": ["JP"],
            "findNotes": [1],
            "notesInfo": [{"fields": {"Word": {"value": "食べる"}}}],
        }.get(action, [])

    _patch_known(monkeypatch, {"known": {"JP": ["Expression"]}}, anki)
    c = doc.check_known()
    assert c.status == "fail"
    assert "Expression" in c.detail and "Word" in c.detail


def test_known_check_ok_when_deck_and_field_present(monkeypatch):
    def anki(action, **_kw):
        return {
            "deckNames": ["JP"],
            "findNotes": [1],
            "notesInfo": [{"fields": {"Word": {"value": "食べる"}}}],
        }.get(action, [])

    _patch_known(monkeypatch, {"known": {"JP": ["Word"]}}, anki)
    assert doc.check_known().status == "ok"


def test_known_check_skips_quietly_when_anki_unreachable(monkeypatch):
    # The `anki` check owns the single "Anki is down" warning; [known] must not warn a second time
    # for the same root cause — it degrades to a hidden info line instead.
    def boom(_action, **_kw):
        raise OSError("connection refused")

    _patch_known(monkeypatch, {"known": {"JP": ["Word"]}}, boom)
    c = doc.check_known()
    assert c.status == "ok" and c.info is True
    assert "unreachable" in c.detail


def test_anki_check_unreachable(monkeypatch):
    def boom(_action, **_kw):
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


def test_free_threading_check():
    # On any interpreter the check must classify itself without error.
    c = doc.check_free_threading()
    assert c.status in {"ok", "warn"}


def test_version_check_reports_overlay_version(monkeypatch):
    import overlay.version as ver

    monkeypatch.setattr(ver, "overlay_version", lambda: "9.9.9")
    c = doc.check_version()
    assert c.name == "version" and c.status == "ok" and "9.9.9" in c.detail


def test_windows_and_powershell_checks_are_ok_off_windows(monkeypatch):
    monkeypatch.setattr(doc.sys, "platform", "darwin")
    win = doc.check_windows()
    assert win.status == "ok" and win.info is True  # useless off Windows → hidden by default
    ps = doc.check_powershell()
    # doesn't shell out off Windows, and its "n/a" line is hidden unless --verbose
    assert ps.status == "ok" and "n/a" in ps.detail and ps.info is True


def test_mpv_socket_check_reports_set_and_unset(monkeypatch):
    monkeypatch.setattr(doc.sys, "platform", "darwin")
    monkeypatch.setattr(doc, "load_config", lambda: {"mpv_socket": r"\\.\pipe\mpvsocket"})
    set_c = doc.check_mpv_socket()
    assert set_c.status == "ok" and "mpvsocket" in set_c.detail
    assert set_c.info is False  # a configured socket is meaningful — shown by default
    monkeypatch.setattr(doc, "load_config", dict)
    unset_c = doc.check_mpv_socket()
    # unset is the norm → an informational hint (not a warn), hidden unless --verbose
    assert unset_c.status == "ok" and "attach to YOUR" in unset_c.detail and unset_c.info is True


def test_mpv_socket_check_warns_about_locale_mangled_windows_pipe(monkeypatch):
    monkeypatch.setattr(doc.sys, "platform", "win32")
    monkeypatch.setattr(doc, "load_config", lambda: {"mpv_socket": "￥￥.￥pipe￥mpvsocket"})

    c = doc.check_mpv_socket()

    assert c.status == "warn"
    assert r"\\.\pipe\mpvsocket" in c.detail


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
    assert c.status in {"ok", "warn"}
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
    (scripts / "saitenka.lua").write_text("args = { 'saitenka', '--attach', sock }\n")
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [scripts])
    c = doc.check_plugin()
    assert c.status == "fail" and "install-plugin" in c.detail


def test_plugin_installed_with_baked_path_is_ok(tmp_path, monkeypatch):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    # a real install bakes the absolute overlay-bin path → doctor sees it as correct
    bin_path = tmp_path / "bin" / "saitenka"
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
        "local SAITENKA_BIN = 'saitenka'\nargs = { SAITENKA_BIN, 'attach', sock }\n"
    )
    monkeypatch.setattr(plugin, "all_scripts_dirs", lambda: [scripts])
    c = doc.check_plugin()
    assert c.status == "fail" and "bare" in c.detail


def test_plugin_baked_path_gone_warns(tmp_path, monkeypatch):
    from overlay.app import plugin

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "saitenka.lua").write_text(
        "local SAITENKA_BIN = [[/nope/saitenka]]\nargs = { SAITENKA_BIN, 'attach', sock }\n"
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
    assert c.status == "ok" and "fuzzy" in c.detail and c.info is True  # safe value → hidden


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


def test_telemetry_check_reports_disabled_by_default(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    cfg.write_text("")
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    c = doc.check_telemetry()
    assert c.status == "ok" and "disabled" in c.detail and c.info is True  # default state → hidden


def test_telemetry_check_enabled_no_trace_yet(tmp_path, monkeypatch):
    cfg = tmp_path / "overlay.toml"
    export = (
        tmp_path / "tel"
    )  # isolate the export dir (empty) so a real ~/.cache trace can't leak in
    # .as_posix(): a Windows path's backslashes are TOML string escapes ("\U"/"\t") → parse failure.
    cfg.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{export.as_posix()}"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    c = doc.check_telemetry()
    assert c.status == "ok" and "no trace yet" in c.detail


def test_telemetry_check_enabled_with_trace_file(tmp_path, monkeypatch):
    export = tmp_path / "telemetry"
    export.mkdir()
    (export / "trace-20260101-000000.json").write_text('{"traceEvents": []}')  # a rotated trace
    cfg = tmp_path / "overlay.toml"
    # .as_posix(): a Windows path's backslashes are TOML string escapes ("\U"/"\t") → parse failure.
    cfg.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{export.as_posix()}"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    c = doc.check_telemetry()
    assert c.status == "ok" and "last trace" in c.detail and "KiB" in c.detail


def test_recent_errors_tails_log(tmp_path, monkeypatch):
    logf = tmp_path / "overlay.log"
    logf.write_text("2026-07-21 A\n2026-07-21 B ERROR boom\n")
    monkeypatch.setattr(doc, "LOG_PATH", logf)
    c = doc.check_recent_errors()
    assert "boom" in c.detail


def test_recent_errors_skips_debug_with_error_in_traceback(tmp_path, monkeypatch):
    # A debug record whose embedded traceback mentions "error" must NOT be surfaced — the level is
    # what decides, not the word appearing anywhere in the line (the Anki-down noise regression).
    debug = json.dumps(
        {
            "event": "cache refresh failed",
            "level": "debug",
            "exception": "ConnectionRefusedError: nope",
        }
    )
    logf = tmp_path / "overlay.log"
    logf.write_text(debug + "\n")
    monkeypatch.setattr(doc, "LOG_PATH", logf)
    c = doc.check_recent_errors()
    assert c.status == "ok" and "no recent errors" in c.detail


def _start_log(*versions: str) -> str:
    """overlay.log with a ``… starting`` line per session, in the structlog-JSON shape the overlay writes."""
    return "".join(
        json.dumps({"event": f"saitenka overlay {v} starting", "level": "info"}) + "\n"
        for v in versions
    )


def _stale_check(tmp_path, monkeypatch, *, installed: str, logged: tuple[str, ...] | None):
    import overlay.version as ver

    monkeypatch.setattr(ver, "overlay_version", lambda: installed)
    logf = tmp_path / "overlay.log"
    if logged is not None:
        logf.write_text(_start_log(*logged))
    monkeypatch.setattr(doc, "LOG_PATH", logf)
    return doc.check_stale_overlay()


def test_stale_overlay_ok_when_running_matches_installed(tmp_path, monkeypatch):
    c = _stale_check(tmp_path, monkeypatch, installed="1.3.0+gABC", logged=("1.3.0+gABC",))
    assert c.status == "ok" and c.info  # a healthy match is hidden info, not green noise


def test_stale_overlay_warns_when_the_running_build_differs(tmp_path, monkeypatch):
    c = _stale_check(tmp_path, monkeypatch, installed="1.3.0+g40bdceb", logged=("1.3.0+gOLD",))
    assert c.status == "warn"
    assert "1.3.0+gOLD" in c.detail and "1.3.0+g40bdceb" in c.detail
    assert "relaunch mpv" in c.detail


def test_stale_overlay_reads_the_last_session(tmp_path, monkeypatch):
    # v1 then a relaunch onto v2 (== installed) → the newest start line wins, no warning.
    c = _stale_check(tmp_path, monkeypatch, installed="v2", logged=("v1", "v2"))
    assert c.status == "ok"


def test_stale_overlay_ignores_a_dirty_suffix_flip(tmp_path, monkeypatch):
    # Same sha, only the volatile -dirty flipped between session start and doctor time → not stale.
    c = _stale_check(tmp_path, monkeypatch, installed="1.3.0+gABC", logged=("1.3.0+gABC-dirty",))
    assert c.status == "ok"


def test_stale_overlay_on_a_non_editable_install_never_crashes_or_false_warns(
    tmp_path, monkeypatch
):
    # Packaged (PyPI) install: overlay_version() is the bare release (no +g suffix), the start line is
    # the same bare version → matches cleanly, no crash.
    c = _stale_check(tmp_path, monkeypatch, installed="1.3.0", logged=("1.3.0",))
    assert c.status == "ok"


def test_stale_overlay_is_quiet_without_a_start_line(tmp_path, monkeypatch):
    logf = tmp_path / "overlay.log"
    logf.write_text(json.dumps({"event": "launching mpv", "level": "info"}) + "\n")
    monkeypatch.setattr(doc, "LOG_PATH", logf)
    c = doc.check_stale_overlay()
    assert c.status == "ok" and c.info  # a log predating the version line must never false-warn


def test_stale_overlay_is_quiet_without_a_log(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "LOG_PATH", tmp_path / "does-not-exist.log")
    c = doc.check_stale_overlay()
    assert c.status == "ok" and c.info


def test_start_log_message_matches_the_doctor_regex():
    # The producer/consumer contract: the message Reader.run() writes ("saitenka overlay <ver> starting")
    # must stay parseable by the guard's regex.
    m = doc._OVERLAY_START_RE.search("saitenka overlay 1.3.0+gABC starting")
    assert m and m.group(1) == "1.3.0+gABC"


def test_recent_errors_collapses_traceback_to_one_line(tmp_path, monkeypatch):
    # A warning record with a multi-line traceback renders as ONE compact line — never the raw dump.
    rec = json.dumps(
        {
            "event": "refresh failed",
            "level": "warning",
            "exception": "Traceback (most recent call last):\n  File x\nURLError: [Errno 61] Connection refused",
        }
    )
    logf = tmp_path / "overlay.log"
    logf.write_text(rec + "\n")
    monkeypatch.setattr(doc, "LOG_PATH", logf)
    c = doc.check_recent_errors()
    assert c.status == "warn"
    assert "Traceback" not in c.detail
    assert "refresh failed — URLError: [Errno 61] Connection refused" in c.detail


def test_run_all_checks_and_json():
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


def test_print_report_hides_info_by_default_shows_with_verbose(capsys):
    report = doc.Report(
        [
            doc.Check("mpv", "ok", "mpv present"),
            doc.Check("windows", "ok", "not Windows (Darwin)", info=True),
            doc.Check("anki", "warn", "AnkiConnect unreachable"),
        ]
    )
    doc.print_report(report)
    default = capsys.readouterr().out
    assert "mpv present" in default and "AnkiConnect unreachable" in default
    assert "not Windows" not in default  # the info line is hidden in the default view

    doc.print_report(report, verbose=True)
    assert "not Windows" in capsys.readouterr().out  # --verbose reveals it


def test_json_carries_info_lines_even_though_default_view_hides_them():
    report = doc.Report([doc.Check("windows", "ok", "not Windows", info=True)])
    j = report.to_json()
    assert j["checks"][0]["info"] is True  # bug reports keep the full set


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
