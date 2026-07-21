"""Automatic crash capture: report writing, secret redaction, retention, hook install, integration."""

from __future__ import annotations

import sys
import threading

from overlay.app import crashlog


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))


def _boom():
    raise ValueError("kaboom")


def _tb() -> str:
    import traceback

    try:
        _boom()
    except ValueError:
        return "".join(traceback.format_exception(*sys.exc_info()))


def test_write_report_captures_traceback_and_redacts_command(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["saitenka-overlay", "run", "--jimaku-key", "SECRETKEY123"])
    p = crashlog.write_report("main-thread", _tb())
    text = p.read_text(encoding="utf-8")
    assert "ValueError: kaboom" in text
    assert "SECRETKEY123" not in text  # the key after --jimaku-key is redacted
    assert "<redacted>" in text
    assert p.name.startswith("crash-") and p.suffix == ".log"


def test_prune_keeps_only_the_most_recent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(crashlog, "_KEEP", 3)
    d = crashlog.crash_dir()
    d.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (d / f"crash-2026010{i}-000000.log").write_text("x")
    crashlog._prune(d)
    assert len(list(d.glob("crash-*.log"))) == 3


def test_excepthook_writes_report_but_ignores_keyboard_interrupt(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)  # swallow the re-raise to stderr
    # KeyboardInterrupt is not a crash → no report
    crashlog._excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert not list(crashlog.crash_dir().glob("crash-*.log"))
    # a real exception → a report
    try:
        _boom()
    except ValueError:
        crashlog._excepthook(*sys.exc_info())
    assert len(list(crashlog.crash_dir().glob("crash-*.log"))) == 1


def test_install_is_idempotent_and_sets_hooks(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(crashlog, "_installed", False)
    orig = sys.excepthook
    try:
        crashlog.install()
        assert sys.excepthook is crashlog._excepthook
        assert threading.excepthook is crashlog._thread_excepthook
        crashlog.install()  # second call is a no-op (no raise)
    finally:
        sys.excepthook = orig
        threading.excepthook = threading.__excepthook__


def test_report_bundle_includes_crash_logs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    crashlog.write_report("main-thread", _tb())
    import overlay.app.doctor as doc

    class _Rep:
        def to_json(self):
            return {"summary": {}, "checks": []}

    monkeypatch.setattr(doc, "run_checks", lambda *a, **k: _Rep())
    from overlay.app import report

    monkeypatch.setattr(report, "_first_line", lambda *c: "mpv v0.40.0")
    members = report.collect(include_log=False)
    assert any(name.startswith("crashes/crash-") for name in members)


def test_doctor_check_crashes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import overlay.app.doctor as doc

    assert doc.check_crashes().status == "ok"  # none yet
    crashlog.write_report("main-thread", _tb())
    c = doc.check_crashes()
    assert c.status == "warn" and "report" in c.detail
