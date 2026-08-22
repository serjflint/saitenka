"""Diagnostics bundle: secret redaction, tiered contents, timestamped zip."""

from __future__ import annotations

import zipfile
from pathlib import Path

from saitenka.app import report


def test_redact_secrets_scrubs_keys_and_tokens():
    assert "<redacted>" in report._redact_secrets('jimaku key = "abcdef123456"')
    assert "<redacted>" in report._redact_secrets("Authorization: Bearer sk-9s8d7f6g5h4j")
    assert "abcdef123456" not in report._redact_secrets('key="abcdef123456"')
    # ordinary text with a short word is untouched
    assert report._redact_secrets("the cat sat") == "the cat sat"


def test_redact_config_blanks_key_lines_keeps_shape():
    cfg = 'enabled = true\nkey = "sekritvalue123"\nresync = true\n'
    red = report._redact_config(cfg)
    assert "sekritvalue123" not in red
    assert '"<redacted>"' in red
    assert "enabled = true" in red and "resync = true" in red  # non-secret lines survive


def _hermetic(monkeypatch, tmp_path):
    """Point config/log/mpv dirs at a fake tree and stub the network-touching doctor + mpv probe."""
    cfg = tmp_path / "overlay.toml"
    cfg.write_text('dicts = ["a.zip"]\n\n[jimaku]\nkey = "TOPSECRETKEY99"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfg))
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "overlay.log").write_text(
        "2026 INFO started\n2026 INFO jimaku key=SHOULDVANISH123\n"
    )
    monkeypatch.setenv("MPV_HOME", str(tmp_path / "mpvhome"))
    # `_collect_player_crashes` reads under the home dir, so leaving HOME real would make every
    # assertion here depend on whether mpv had crashed on the machine running the suite.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    class _Rep:
        def to_json(self):
            return {"summary": {"ok": 1, "warn": 0, "fail": 0}, "checks": []}

    monkeypatch.setattr(report, "_first_line", lambda *_c: "mpv v0.40.0")
    import saitenka.app.doctor as doc

    monkeypatch.setattr(doc, "run_checks", lambda *_a, **_k: _Rep())
    return cfg


def test_collect_includes_expected_members_and_redacts(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    members = report.collect(include_log=True)
    assert "versions.txt" in members and "doctor.json" in members
    assert "overlay.toml" in members and "MANIFEST.txt" in members and "overlay.log" in members
    # secrets gone from both config and log
    assert "TOPSECRETKEY99" not in members["overlay.toml"]
    assert "SHOULDVANISH123" not in members["overlay.log"]
    # manifest carries the privacy note
    assert "NEVER uploaded" in members["MANIFEST.txt"]


def test_collect_bundles_the_mpv_binding_table(monkeypatch, tmp_path):
    """A command mpv attributes to a key binding is only excludable as ours against `input.conf`,
    so the bundle has to carry it beside `mpv.conf` — reading it off the reporter's disk is not an
    option once the report has left the machine."""
    _hermetic(monkeypatch, tmp_path)
    mpv_home = tmp_path / "mpvhome"
    mpv_home.mkdir()
    (mpv_home / "mpv.conf").write_text("hwdec=auto-safe\n")
    (mpv_home / "input.conf").write_text(f"MBTN_LEFT cycle pause\np run {tmp_path}/tool\n")

    members = report.collect(include_log=False)

    assert members["mpv/mpvhome.input.conf"].startswith("MBTN_LEFT cycle pause")
    assert "hwdec=auto-safe" in members["mpv/mpvhome.mpv.conf"]


def test_collect_scrubs_home_from_the_binding_table(monkeypatch, tmp_path):
    """Negative control for the test above: a bind naming a path under $HOME must not ship the
    username, the same treatment every other bundled text gets."""
    _hermetic(monkeypatch, tmp_path)
    mpv_home = tmp_path / "mpvhome"
    mpv_home.mkdir()
    home = tmp_path / "home"
    (mpv_home / "input.conf").write_text(f"F5 run {home}/scripts/thing.sh\n")

    bundled = report.collect(include_log=False)["mpv/mpvhome.input.conf"]

    assert str(home) not in bundled
    assert "scripts/thing.sh" in bundled  # scrubbed, not dropped


def test_collect_no_log_excludes_log(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    members = report.collect(include_log=False)
    assert "overlay.log" not in members
    assert "mpv.log" not in members  # mpv log gated by the same --no-log
    assert "no (--no-log)" in members["MANIFEST.txt"]


def test_collect_includes_dict_listing_and_mpv_log(monkeypatch, tmp_path):
    """Report surfaces the imported-dictionary inventory (from the consolidated DB) and mpv's own log —
    the diagnostics that would have made this session's dict + mpv issues obvious."""
    import dicthelp

    _hermetic(monkeypatch, tmp_path)
    z = dicthelp.term_zip(tmp_path / "my.zip", "MyDict", [["猫", "ねこ", ["cat"]]])
    dicthelp.db().import_zip(z, imported_at=dicthelp.AT)  # into the per-test hermetic DB
    (tmp_path / "cache" / "mpv.log").write_text("[cplayer] mpv 0.40 started\n")

    members = report.collect(include_log=True)
    listing = members["dicts.listing.txt"]
    assert "MyDict" in listing  # imported dictionary listed
    assert "schema 1" in listing  # header carries schema + size (content-free)
    assert "entries=1" in listing  # per-table counts — a missing tags table is now visible
    assert "mpv.log" in members and "mpv 0.40 started" in members["mpv.log"]


def test_collect_bundles_telemetry_trace_when_enabled_and_present(monkeypatch, tmp_path):
    """Stage 10: the CTF trace a LIVE session wrote to disk is bundled — collect() runs in its own
    process, so it reads the file, not any in-memory metrics state. Home path gets scrubbed like
    every other bundled artifact (span attributes only ever carry a dict title + hex ids today —
    never a secret — so home-path scrubbing is what's realistically exercisable here)."""
    cfg = _hermetic(monkeypatch, tmp_path)
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    home = str(Path.home())
    (tel_dir / "trace-20260101-000000.json").write_text(  # a rotated per-session trace
        '{"traceEvents": [{"name": "op", "args": {"dict": "' + home + '/mydict"}}]}'
    )
    # .as_posix(): a Windows path's backslashes are TOML string escapes → the table would fail to parse.
    cfg.write_text(
        cfg.read_text() + f'\n[telemetry]\nenabled = true\nexport_dir = "{tel_dir.as_posix()}"\n'
    )

    members = report.collect(include_log=True)
    assert "telemetry/trace.json" in members
    assert "op" in members["telemetry/trace.json"]
    assert home not in members["telemetry/trace.json"]
    assert "<HOME>" in members["telemetry/trace.json"]


def test_collect_omits_telemetry_when_disabled(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    members = report.collect(include_log=True)
    assert not any(name.startswith("telemetry/") for name in members)


def test_build_report_bundle_writes_timestamped_zip(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    out = tmp_path / "reports"
    dest = report.build_report_bundle(out, timestamp="20260721-160000")
    assert dest.name == "saitenka-report-20260721-160000.zip"
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "MANIFEST.txt" in names and "doctor.json" in names
        assert "TOPSECRETKEY99" not in zf.read("overlay.toml").decode()


def test_scrub_home_replaces_home_path_and_username(monkeypatch):
    import getpass
    from pathlib import Path

    monkeypatch.setattr(getpass, "getuser", lambda: "leodu")
    text = f"opened {Path.home()}/Videos and user leodu ran it"
    out = report._scrub_home(text)
    assert "<HOME>" in out and "<USER>" in out
    assert str(Path.home()) not in out and "leodu" not in out


def test_latest_session_reads_the_last_stamped_run():
    log = '{"event":"a","session":"120000-aa11"}\nnot json\n{"event":"b","session":"130000-bb22"}\n'
    assert report._latest_session(log) == "130000-bb22"  # newest run, tolerant of non-JSON lines


def test_latest_session_is_none_for_a_pre_session_log():
    assert report._latest_session('{"event":"old"}\n') is None


def test_manifest_surfaces_the_latest_session():
    out = report._manifest({"overlay.log": "x"}, include_log=True, session="140000-cc33")
    assert "latest session: 140000-cc33" in out


def _player_crash(tmp_path: Path, name: str, body: str, *, age_s: float = 0.0) -> Path:
    reports = tmp_path / "home" / report._MACOS_CRASH_REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / name
    path.write_text(body)
    if age_s:
        import os

        stamp = path.stat().st_mtime - age_s
        os.utime(path, (stamp, stamp))
    return path


def test_collect_bundles_the_players_native_crash_report(monkeypatch, tmp_path):
    """The frame that names *where* mpv died — the artifact two SIGBUS investigations needed and the
    bundle could not carry."""
    monkeypatch.setattr(report.sys, "platform", "darwin")
    _hermetic(monkeypatch, tmp_path)
    _player_crash(
        tmp_path,
        "mpv-2026-08-22-144118.ips",
        '{"app_name":"mpv"}\n{"exception":{"signal":"SIGBUS"},"key":"LEAKYKEY42",'
        f'"procPath":"{Path.home()}/bin/mpv"}}\n',
    )

    members = report.collect(include_log=True)

    assert "crashes/player/mpv-2026-08-22-144118.ips" in members
    body = members["crashes/player/mpv-2026-08-22-144118.ips"]
    assert "SIGBUS" in body, "the faulting signal is the reason this member exists"
    # Same redactor as every other member: home paths scrubbed, JSON-quoted secrets scrubbed.
    assert str(Path.home()) not in body and "<HOME>" in body
    assert "LEAKYKEY42" not in body


def test_collect_omits_player_crash_reports_from_another_day(monkeypatch, tmp_path):
    monkeypatch.setattr(report.sys, "platform", "darwin")
    _hermetic(monkeypatch, tmp_path)
    _player_crash(tmp_path, "mpv-old.ips", "{}\n", age_s=report._PLAYER_CRASH_MAX_AGE_S + 60)

    assert "crashes/player/mpv-old.ips" not in report.collect(include_log=True)


def test_collect_omits_player_crash_reports_off_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(report.sys, "platform", "linux")
    _hermetic(monkeypatch, tmp_path)
    _player_crash(tmp_path, "mpv-2026-08-22-144118.ips", "{}\n")

    assert not [m for m in report.collect(include_log=True) if m.startswith("crashes/player/")]


def test_collect_bundles_a_shutdown_thread_dump(monkeypatch, tmp_path):
    """A dump file exists only when an exit hung, so carrying it is how the next report says so."""
    _hermetic(monkeypatch, tmp_path)
    crashes = tmp_path / "cache" / "crashes"
    crashes.mkdir(parents=True)
    (crashes / "shutdown-hang-20260822-144132.log").write_text("Timeout (0:00:03)!\nThread 0x1 …\n")

    members = report.collect(include_log=True)

    assert "Timeout" in members["crashes/shutdown-hang-20260822-144132.log"]


def test_redact_secrets_scrubs_json_quoted_keys():
    """`telemetry/trace.json` and mpv's `.ips` are JSON; a name in quotes never reached the separator.

    Scoped to the quoting, not the naming: an underscore-prefixed name (`jimaku_key`) still escapes,
    in TOML as much as in JSON, because `\\b` does not fall between `u` and `k`. Widening the name
    set trades that against redacting every `sort_key` in a trace, and is its own decision.
    """
    out = report._redact_secrets('{"api_key":"zzzzzzzz", "token": "abcdef123456"}')
    assert "zzzzzzzz" not in out and "abcdef123456" not in out
    assert out.count("<redacted>") == 2
