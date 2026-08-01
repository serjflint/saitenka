"""Auto-launch Anki for mining/coloring — try to start it, warn (never raise) if it won't come up."""

from __future__ import annotations

import sys

from overlay.app import anki as anki_mod


def test_ensure_returns_immediately_when_reachable(monkeypatch):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: True)
    launched = []
    monkeypatch.setattr(anki_mod.subprocess, "Popen", lambda *a, **_k: launched.append(a))
    assert anki_mod.ensure_anki_running() is True
    assert launched == []  # already up → no launch attempt


def test_ensure_launches_when_down_then_comes_up(monkeypatch):
    calls = {"n": 0, "launched": None}

    def reachable(*_a, **_k):  # down on the pre-check, up on the first poll
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(anki_mod, "anki_reachable", reachable)
    monkeypatch.setattr(anki_mod.sys, "platform", "darwin")
    monkeypatch.setattr(anki_mod.subprocess, "Popen", lambda cmd, **_k: calls.update(launched=cmd))
    monkeypatch.setattr(anki_mod.time, "sleep", lambda _s: None)
    assert anki_mod.ensure_anki_running(wait=5) is True
    assert calls["launched"] == ["open", "-a", "Anki"]


def test_ensure_returns_false_when_launch_fails(monkeypatch):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_a, **_k: False)

    def boom(*_a, **_k):
        raise OSError("no such app")

    monkeypatch.setattr(anki_mod.subprocess, "Popen", boom)
    assert anki_mod.ensure_anki_running(wait=1) is False  # warned + degraded, did not raise


def test_windows_launches_discovered_anki_directly(tmp_path, monkeypatch):
    exe = tmp_path / "anki.exe"
    exe.write_bytes(b"")
    calls = {"reachable": 0, "launch": None}

    def reachable(*_args, **_kwargs):
        calls["reachable"] += 1
        return calls["reachable"] > 1

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(anki_mod, "anki_reachable", reachable)
    monkeypatch.setattr(anki_mod, "find_anki", lambda: str(exe))
    monkeypatch.setattr(
        anki_mod.subprocess, "Popen", lambda command, **_kwargs: calls.update(launch=command)
    )
    monkeypatch.setattr(anki_mod.time, "sleep", lambda _seconds: None)

    assert anki_mod.ensure_anki_running(wait=1) is True
    assert calls["launch"] == [str(exe)]


def test_windows_missing_anki_fails_without_shell_or_poll(monkeypatch):
    launches = []
    sleeps = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(anki_mod, "find_anki", lambda: None)
    monkeypatch.setattr(
        anki_mod.subprocess, "Popen", lambda *args, **_kwargs: launches.append(args)
    )
    monkeypatch.setattr(anki_mod.time, "sleep", sleeps.append)

    assert anki_mod.ensure_anki_running(wait=20) is False
    assert launches == [] and sleeps == []


def test_find_anki_prefers_configured_executable(tmp_path):
    exe = tmp_path / "Anki custom.exe"
    exe.write_bytes(b"")
    assert anki_mod.find_anki({"anki": {"executable": str(exe)}}) == str(exe)
