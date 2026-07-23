"""Diagnostics bundle: secret redaction, tiered contents, timestamped zip."""

from __future__ import annotations

import zipfile
from pathlib import Path

from overlay.app import report


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

    class _Rep:
        def to_json(self):
            return {"summary": {"ok": 1, "warn": 0, "fail": 0}, "checks": []}

    monkeypatch.setattr(report, "_first_line", lambda *c: "mpv v0.40.0")
    import overlay.app.doctor as doc

    monkeypatch.setattr(doc, "run_checks", lambda *a, **k: _Rep())
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
    assert "MyDict" in members["dicts.listing.txt"]  # imported dictionary listed
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
    (tel_dir / "trace.json").write_text(
        '{"traceEvents": [{"name": "op", "args": {"dict": "' + home + '/mydict"}}]}'
    )
    cfg.write_text(cfg.read_text() + f'\n[telemetry]\nenabled = true\nexport_dir = "{tel_dir}"\n')

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
