"""SubMiner-conflict detection: the overlay must step aside when SubMiner is running (both inject an
mpv overlay → flicker / stuck 'overlay loading'), and doctor must flag it."""

from __future__ import annotations

from overlay.app import conflicts
from overlay.app import doctor as doc


def test_subminer_running_true_on_pgrep_hit(monkeypatch):
    monkeypatch.setattr(conflicts.sys, "platform", "darwin")

    class R:
        returncode = 0

    monkeypatch.setattr(conflicts.subprocess, "run", lambda *a, **k: R())
    assert conflicts.subminer_running() is True


def test_subminer_running_false_on_pgrep_miss(monkeypatch):
    monkeypatch.setattr(conflicts.sys, "platform", "darwin")

    class R:
        returncode = 1

    monkeypatch.setattr(conflicts.subprocess, "run", lambda *a, **k: R())
    assert conflicts.subminer_running() is False


def test_doctor_warns_when_subminer_running(monkeypatch):
    monkeypatch.setattr("overlay.app.conflicts.subminer_running", lambda: True)
    c = doc.check_subminer_conflict()
    assert c.status == "warn" and "SubMiner is RUNNING" in c.detail


def test_doctor_ok_when_subminer_absent(monkeypatch):
    monkeypatch.setattr("overlay.app.conflicts.subminer_running", lambda: False)
    monkeypatch.setattr("overlay.app.conflicts.subminer_installed", lambda: False)
    c = doc.check_subminer_conflict()
    assert c.status == "ok"
