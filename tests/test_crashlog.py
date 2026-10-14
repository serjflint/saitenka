"""Automatic crash capture: report writing, secret redaction, retention, hook install, integration."""

from __future__ import annotations

import sys

import pytest

from saitenka.app import crashlog


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fault injection disables core files")
def test_isolated_native_fault_reaches_the_report_bundle(monkeypatch, tmp_path):
    import os
    import subprocess

    from test_report import _hermetic

    from saitenka.app import report

    _hermetic(monkeypatch, tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import resource, os; resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
                "from saitenka.app import crashlog; crashlog.install(); os.abort()"
            ),
        ],
        env=dict(os.environ),
        capture_output=True,
        timeout=3,
        check=False,
    )
    managed = crashlog.write_report("main-thread", "managed failure")
    members = report.collect(diagnostic_detail=True)
    assert result.returncode < 0
    assert "Fatal Python error" in members["crashes/faulthandler.log"]
    assert f"crashes/{managed.name}" in members
    from test_diagnostic_findings import _load_findings

    bundle = report.build_report_bundle(
        tmp_path / "reports", timestamp="native-fault", diagnostic_detail=True
    )
    diagnosis = _load_findings().diagnose_report(bundle)
    fault = next(
        row for row in diagnosis["findings"] if row["category"] == "historical-native-fault"
    )
    assert fault["severity"] == "warning"
    assert fault["evidence"] == {"scope": "historical", "session": "unknown"}
    assert diagnosis["fidelity"]["status"] == "unknown"


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
    monkeypatch.setattr(sys, "argv", ["saitenka", "run", "--jimaku-key", "SECRETKEY123"])
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
    monkeypatch.setattr(sys, "__excepthook__", lambda *_a: None)  # swallow the re-raise to stderr
    # KeyboardInterrupt is not a crash → no report
    crashlog._excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert not list(crashlog.crash_dir().glob("crash-*.log"))
    # a real exception → a report
    try:
        _boom()
    except ValueError:
        crashlog._excepthook(*sys.exc_info())
    assert len(list(crashlog.crash_dir().glob("crash-*.log"))) == 1


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_install_captures_uncaught_main_and_worker_failures(tmp_path):
    import os
    import subprocess

    cache = tmp_path / "cache"
    code = """
import threading
from saitenka.app import crashlog

crashlog.install()
crashlog.install()

def fail_worker():
    raise RuntimeError("worker-probe")

worker = threading.Thread(target=fail_worker, name="probe-thread")
worker.start()
worker.join()
raise ValueError("main-probe")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "SAITENKA_CACHE_DIR": str(cache)},
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )

    reports = [path.read_text(encoding="utf-8") for path in (cache / "crashes").glob("crash-*.log")]
    assert result.returncode != 0
    assert len(reports) == 2
    assert any("worker-probe" in report and "thread: probe-thread" in report for report in reports)
    assert any("main-probe" in report and "crash (main-thread)" in report for report in reports)


def test_doctor_check_crashes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import saitenka.app.doctor as doc

    assert doc.check_crashes().status == "ok"  # none yet
    crashlog.write_report("main-thread", _tb())
    c = doc.check_crashes()
    assert c.status == "warn" and "report" in c.detail
