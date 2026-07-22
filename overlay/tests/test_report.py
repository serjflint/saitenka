"""Diagnostics bundle: secret redaction, tiered contents, timestamped zip."""

from __future__ import annotations

import zipfile

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
    """Report surfaces the on-disk dict inventory (data zips + built indexes) and mpv's own log — the
    diagnostics that would have made this session's dict-registration + mpv issues obvious."""
    _hermetic(monkeypatch, tmp_path)
    ddir = tmp_path / "data" / "dicts"
    ddir.mkdir(parents=True)
    (ddir / "MyDict.zip").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr("overlay.app.config.dicts_data_dir", lambda: ddir)  # DATA_HOME is frozen
    idx = tmp_path / "cache" / "dicts"  # cache dir honors $SAITENKA_CACHE_DIR live
    idx.mkdir(parents=True)
    (idx / "MyDict-1-2-v2.sqlite").write_bytes(b"")
    (tmp_path / "cache" / "mpv.log").write_text("[cplayer] mpv 0.40 started\n")

    members = report.collect(include_log=True)
    assert "MyDict.zip" in members["dicts.listing.txt"]  # data-dir zip listed
    assert "MyDict-1-2-v2.sqlite" in members["dicts.listing.txt"]  # built index listed
    assert "mpv.log" in members and "mpv 0.40 started" in members["mpv.log"]


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
