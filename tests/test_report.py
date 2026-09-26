"""Diagnostics bundle: secret redaction, tiered contents, timestamped zip."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from saitenka_dict.schema import SCHEMA_VERSION

from saitenka.app import report
from saitenka.app.log_privacy import LOG_FORMAT


def test_detailed_report_bundles_only_its_frame_session(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    (tmp_path / "cache" / "overlay.log").write_text('{"session":"wanted"}\n')
    (tmp_path / "cache" / "mpv-frame-wanted.json").write_text(
        json.dumps({"session": "wanted", "subtitle_clock": {"sub-delay": -6}})
    )
    (tmp_path / "cache" / "mpv-frame-wanted.tsv").write_text(
        "# health recorded=0 attempted=0 overflow=0\n"
    )

    members = report.collect(diagnostic_detail=True)

    assert "diagnostics/mpv-frame.tsv" in members
    assert json.loads(members["diagnostics/mpv-frame.json"])["subtitle_clock"]["sub-delay"] == -6
    assert "diagnostics/mpv-frame.tsv" not in report.collect(diagnostic_detail=False)


def test_rotation_losing_a_segment_does_not_abort_collection(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    active = tmp_path / "overlay.log"
    active.write_text('{"session":"wanted"}\n')
    rotated = tmp_path / "overlay.log.1"
    rotated.write_text('{"session":"wanted"}\n')
    original = Path.open

    def open_during_rotation(path, *args, **kwargs):
        if path == rotated:
            raise FileNotFoundError("rotated during capture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_during_rotation)
    session, members = report._collect_logs(active, include=True)

    assert session == "wanted"
    sources = json.loads(members["logs/collection.json"])["sources"]
    assert sources[0]["status"] == "collected"
    assert sources[1]["status"] == "unavailable"
    assert "logs/overlay.log.1" not in members


def test_native_fault_is_bundled_redacted(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    from saitenka.app import paths

    directory = tmp_path / "crashes"
    directory.mkdir()
    monkeypatch.setattr(paths, "crash_dir", lambda: directory)
    (directory / "faulthandler.log").write_text(
        "Fatal Python error: Segmentation fault\npassword=secretvalue99"
    )

    members = report.collect(diagnostic_detail=True)

    assert "Segmentation fault" in members["crashes/faulthandler.log"]
    assert "secretvalue99" not in members["crashes/faulthandler.log"]


def test_trace_selection_matches_log_session_not_newest_file(monkeypatch, tmp_path):
    cfg = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    cfg.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{directory.as_posix()}"\n')
    for number, session in enumerate(("wanted", "unrelated")):
        (directory / f"trace-{number}.json").write_text(
            json.dumps({"otherData": {"session": session}, "traceEvents": []})
        )

    members = report._collect_telemetry("wanted")

    assert json.loads(members["telemetry/trace.json"])["otherData"]["session"] == "wanted"
    assert (
        json.loads(members["telemetry/collection.json"])["candidates"][0]["status"]
        == "session-mismatch"
    )


def test_partial_trace_is_unavailable_not_empty_healthy(monkeypatch, tmp_path):
    cfg = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    cfg.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{directory.as_posix()}"\n')
    (directory / "trace-1.json").write_text('{"traceEvents":[')

    members = report._collect_telemetry("wanted")

    assert "telemetry/trace.json" not in members
    assert (
        json.loads(members["telemetry/collection.json"])["candidates"][0]["status"]
        == "unreadable-or-concurrent-write"
    )


def test_health_survives_missing_trace(monkeypatch, tmp_path):
    cfg = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    cfg.write_text(f'[telemetry]\nexport_dir = "{directory.as_posix()}"\n')
    (directory / "trace-1.health.json").write_text(
        json.dumps({"session": "wanted", "lost_events": 9})
    )

    members = report._collect_telemetry("wanted")

    assert "telemetry/trace.json" not in members
    assert json.loads(members["telemetry/health.json"])["lost_events"] == 9


def test_fallback_trace_cannot_borrow_newer_sessions_health(monkeypatch, tmp_path):
    cfg = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    cfg.write_text(f'[telemetry]\nexport_dir = "{directory.as_posix()}"\n')
    (directory / "trace-2.json").write_text('{"traceEvents":[')
    (directory / "trace-2.health.json").write_text(json.dumps({"session": "new", "lost_events": 0}))
    (directory / "trace-1.json").write_text(
        json.dumps({"otherData": {"session": "old"}, "traceEvents": []})
    )
    (directory / "trace-1.health.json").write_text(json.dumps({"session": "old", "lost_events": 9}))

    members = report._collect_telemetry(None)

    assert json.loads(members["telemetry/health.json"])["session"] == "old"
    assert json.loads(members["telemetry/health.json"])["lost_events"] == 9
    assert json.loads(members["telemetry/health-collection.json"])["trace_source"] == "trace-1.json"


def test_redact_secrets_scrubs_keys_and_tokens():
    assert "<redacted>" in report._redact_secrets('jimaku key = "abcdef123456"')
    assert "<redacted>" in report._redact_secrets("Authorization: Bearer sk-9s8d7f6g5h4j")
    assert "abcdef123456" not in report._redact_secrets('key="abcdef123456"')
    # ordinary text with a short word is untouched
    assert report._redact_secrets("the cat sat") == "the cat sat"


def test_scrub_home_redacts_json_encoded_windows_path(monkeypatch):
    home = r"C:\Users\Jäne"
    monkeypatch.setattr(Path, "home", lambda: Path(home))

    for ensure_ascii in (True, False):
        redacted = report._scrub_home(
            json.dumps({"path": home + r"\dict"}, ensure_ascii=ensure_ascii)
        )

        assert json.loads(redacted) == {"path": r"<HOME>\dict"}


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
    # assertion here depend on whether mpv had crashed on the machine running the suite. `USERPROFILE`
    # as well because `Path.home()` reads that one on Windows and ignores HOME — setting only HOME left
    # the isolation silently not applied there, which is worse than the assertion that then failed.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
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
    members = report.collect(include_log=True, diagnostic_detail=True)
    assert "versions.txt" in members and "doctor.json" in members
    assert "overlay.toml" in members and "MANIFEST.txt" in members and "overlay.log" in members
    # secrets gone from both config and log
    assert "TOPSECRETKEY99" not in members["overlay.toml"]
    assert "SHOULDVANISH123" not in members["overlay.log"]
    # manifest carries the privacy note
    assert "NEVER uploaded" in members["MANIFEST.txt"]


def test_doctor_native_output_is_bundled_instead_of_printed(monkeypatch, capfd):
    class Report:
        @staticmethod
        def to_json():
            return {"summary": {"ok": 1, "warn": 0, "fail": 0}, "checks": []}

    from saitenka.app import doctor

    def run_checks():
        os.write(2, b"[ass] libass source: bundled\n")
        return Report()

    monkeypatch.setattr(doctor, "run_checks", run_checks)

    members = report._collect_doctor()

    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""
    assert "[ass] libass source: bundled" in members["doctor-output.txt"]


def test_collect_bundles_the_mpv_binding_table(monkeypatch, tmp_path):
    """A command mpv attributes to a key binding is only excludable as ours against `input.conf`,
    so the bundle has to carry it beside `mpv.conf` — reading it off the reporter's disk is not an
    option once the report has left the machine."""
    _hermetic(monkeypatch, tmp_path)
    mpv_home = tmp_path / "mpvhome"
    mpv_home.mkdir()
    (mpv_home / "mpv.conf").write_text("hwdec=auto-safe\n")
    (mpv_home / "input.conf").write_text(f"MBTN_LEFT cycle pause\np run {tmp_path}/tool\n")

    members = report.collect(include_log=False, diagnostic_detail=True)

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

    bundled = report.collect(include_log=False, diagnostic_detail=True)["mpv/mpvhome.input.conf"]

    assert str(home) not in bundled
    assert "scripts/thing.sh" in bundled  # scrubbed, not dropped


def test_collect_no_log_excludes_log(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    members = report.collect(include_log=False, diagnostic_detail=True)
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

    members = report.collect(include_log=True, diagnostic_detail=True)
    listing = members["dicts.listing.txt"]
    assert "MyDict" in listing  # imported dictionary listed
    assert f"schema {SCHEMA_VERSION}" in listing  # header carries schema + size (content-free)
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
        json.dumps({"traceEvents": [{"name": "op", "args": {"dict": home + "/mydict"}}]})
    )
    # .as_posix(): a Windows path's backslashes are TOML string escapes → the table would fail to parse.
    cfg.write_text(
        cfg.read_text() + f'\n[telemetry]\nenabled = true\nexport_dir = "{tel_dir.as_posix()}"\n'
    )

    members = report.collect(include_log=True, diagnostic_detail=True)
    assert "telemetry/trace.json" in members
    assert "op" in members["telemetry/trace.json"]
    assert home not in members["telemetry/trace.json"]
    assert "<HOME>" in members["telemetry/trace.json"]


def test_collect_omits_telemetry_when_disabled(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    members = report.collect(include_log=True, diagnostic_detail=True)
    assert "telemetry/trace.json" not in members
    assert json.loads(members["telemetry/collection.json"])["status"] == "unavailable"


def test_build_report_bundle_writes_timestamped_zip(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    out = tmp_path / "reports"
    dest = report.build_report_bundle(out, timestamp="20260721-160000", diagnostic_detail=True)
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
    monkeypatch.setattr(report.platform, "system", lambda: "Darwin")
    _hermetic(monkeypatch, tmp_path)
    _player_crash(
        tmp_path,
        "mpv-2026-08-22-144118.ips",
        '{"app_name":"mpv"}\n{"exception":{"signal":"SIGBUS"},"key":"LEAKYKEY42",'
        f'"procPath":"{Path.home()}/bin/mpv"}}\n',
    )

    members = report.collect(include_log=True, diagnostic_detail=True)

    assert "crashes/player/mpv-2026-08-22-144118.ips" in members
    body = members["crashes/player/mpv-2026-08-22-144118.ips"]
    assert "SIGBUS" in body, "the faulting signal is the reason this member exists"
    # Same redactor as every other member: home paths scrubbed, JSON-quoted secrets scrubbed.
    assert str(Path.home()) not in body and "<HOME>" in body
    assert "LEAKYKEY42" not in body


def test_collect_omits_player_crash_reports_from_another_day(monkeypatch, tmp_path):
    monkeypatch.setattr(report.platform, "system", lambda: "Darwin")
    _hermetic(monkeypatch, tmp_path)
    _player_crash(tmp_path, "mpv-old.ips", "{}\n", age_s=report._PLAYER_CRASH_MAX_AGE_S + 60)

    assert "crashes/player/mpv-old.ips" not in report.collect(
        include_log=True, diagnostic_detail=True
    )


def test_collect_omits_player_crash_reports_off_macos(monkeypatch, tmp_path):
    # `platform.system`, matching what the code reads: `sys.platform` is narrowed by the type
    # checkers, so gating on it made every following line unreachable on a Linux `poe types`.
    monkeypatch.setattr(report.platform, "system", lambda: "Linux")
    _hermetic(monkeypatch, tmp_path)
    _player_crash(tmp_path, "mpv-2026-08-22-144118.ips", "{}\n")

    assert not [
        m
        for m in report.collect(include_log=True, diagnostic_detail=True)
        if m.startswith("crashes/player/")
    ]


def test_collect_bundles_a_shutdown_thread_dump(monkeypatch, tmp_path):
    """A dump file exists only when an exit hung, so carrying it is how the next report says so."""
    _hermetic(monkeypatch, tmp_path)
    crashes = tmp_path / "cache" / "crashes"
    crashes.mkdir(parents=True)
    (crashes / "shutdown-hang-20260822-144132.log").write_text("Timeout (0:00:03)!\nThread 0x1 …\n")

    members = report.collect(include_log=True, diagnostic_detail=True)

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


def _record(session: str, event: str, *, log_format: int = LOG_FORMAT) -> str:
    return json.dumps({"session": session, "event": event, "log_format": log_format})


def test_default_report_ships_only_the_reported_sessions_lines(monkeypatch, tmp_path):
    _hermetic(monkeypatch, tmp_path)
    cache = tmp_path / "cache"
    (cache / "overlay.log.1").write_text(
        "\n".join([_record("earlier", "other run"), _record("latest", "first half")]) + "\n"
    )
    (cache / "overlay.log").write_text(
        "\n".join([_record("latest", "second half"), "not json", _record("latest", "end")]) + "\n"
    )

    members = report.collect()

    events = [json.loads(line)["event"] for line in members["overlay.log"].splitlines()]
    assert events == ["first half", "second half", "end"]


def test_session_logged_in_an_older_format_ships_no_log_or_trace(monkeypatch, tmp_path):
    cfg = _hermetic(monkeypatch, tmp_path)
    directory = tmp_path / "telemetry"
    directory.mkdir()
    cfg.write_text(f'[telemetry]\nenabled = true\nexport_dir = "{directory.as_posix()}"\n')
    (directory / "trace-1.json").write_text(
        json.dumps({"otherData": {"session": "old"}, "traceEvents": []})
    )
    (tmp_path / "cache" / "overlay.log").write_text(
        _record("old", "sub index: 3 cues from Show - 01.ass", log_format=LOG_FORMAT - 1) + "\n"
    )

    members = report.collect()

    assert "overlay.log" not in members
    assert "telemetry/trace.json" not in members
    assert json.loads(members["logs/collection.json"])["status"] == "predates-sanitised-format"
