"""Auto-launch Anki for mining/coloring — try to start it, warn (never raise) if it won't come up."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from saitenka.app import anki as anki_mod

_REAL_LAUNCH = anki_mod.launch_anki  # captured before the conftest down-fixture stubs it


def _fake_open(calls: dict, *, returncode: int = 0, stderr: str = ""):
    """Stand in for `subprocess.run(["open", "-a", "Anki"], …)` (the macOS launch path)."""

    def run(cmd, **_kwargs):
        calls["launched"] = cmd
        return SimpleNamespace(returncode=returncode, stderr=stderr)

    return run


@pytest.fixture(autouse=True)
def _restore_real_launch(monkeypatch):
    """Undo the suite's global down-state stub while exercising the real launcher."""
    monkeypatch.setattr(anki_mod, "launch_anki", _REAL_LAUNCH)


def test_windows_launches_discovered_anki_directly(tmp_path, monkeypatch):
    exe = tmp_path / "anki.exe"
    exe.write_bytes(b"")
    calls = {"launch": None}

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(anki_mod, "find_anki", lambda: str(exe))
    monkeypatch.setattr(
        anki_mod.subprocess, "Popen", lambda command, **_kwargs: calls.update(launch=command)
    )
    assert anki_mod.launch_anki() is True
    assert calls["launch"] == [str(exe)]


def test_windows_missing_anki_fails_without_shell_or_poll(monkeypatch):
    launches = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(anki_mod, "find_anki", lambda: None)
    monkeypatch.setattr(
        anki_mod.subprocess, "Popen", lambda *args, **_kwargs: launches.append(args)
    )
    assert anki_mod.launch_anki() is False
    assert launches == []


def test_find_anki_prefers_configured_executable(tmp_path):
    exe = tmp_path / "Anki custom.exe"
    exe.write_bytes(b"")
    assert anki_mod.find_anki({"anki": {"executable": str(exe)}}) == str(exe)


def test_launch_anki_is_fire_and_forget_without_polling(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(anki_mod.subprocess, "run", _fake_open(calls))
    reachable = []
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: reachable.append(True))
    assert anki_mod.launch_anki() is True
    assert calls["launched"] == ["open", "-a", "Anki"]
    assert reachable == []  # launch never polls — that's the watcher's job


def test_launch_anki_false_when_open_raises(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def boom(*_a, **_k):
        raise OSError("no such app")

    monkeypatch.setattr(anki_mod.subprocess, "run", boom)
    assert anki_mod.launch_anki() is False


def test_launch_anki_false_when_open_reports_missing_app(monkeypatch):
    # The real regression: `open -a Anki` exits non-zero when the app isn't found. Popen never saw
    # that (fire-and-forget always looked like success); `run` + returncode does — so we degrade + warn.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        anki_mod.subprocess,
        "run",
        _fake_open({}, returncode=1, stderr="Unable to find application named 'Anki'"),
    )
    assert anki_mod.launch_anki() is False


def test_is_unreachable_flags_anki_down_but_not_app_errors():
    # Connection refused from either the client or a raw urllib call is "Anki isn't running" —
    # expected, so callers suppress the traceback. A real app error (bad model) is not.
    assert anki_mod.is_unreachable(anki_mod._AnkiRetryable("unreachable"))
    assert anki_mod.is_unreachable(ConnectionRefusedError(61, "refused"))
    assert not anki_mod.is_unreachable(anki_mod.AnkiError("model not found"))
    assert not anki_mod.is_unreachable(ValueError("boom"))
