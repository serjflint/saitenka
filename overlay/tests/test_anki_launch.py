"""Auto-launch Anki for mining/coloring — try to start it, warn (never raise) if it won't come up."""

from __future__ import annotations

from overlay.app import anki as anki_mod


def test_ensure_returns_immediately_when_reachable(monkeypatch):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *a, **k: True)
    launched = []
    monkeypatch.setattr(anki_mod.subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert anki_mod.ensure_anki_running() is True
    assert launched == []  # already up → no launch attempt


def test_ensure_launches_when_down_then_comes_up(monkeypatch):
    calls = {"n": 0, "launched": None}

    def reachable(*a, **k):  # down on the pre-check, up on the first poll
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(anki_mod, "anki_reachable", reachable)
    monkeypatch.setattr(anki_mod.subprocess, "Popen", lambda cmd, **k: calls.update(launched=cmd))
    monkeypatch.setattr(anki_mod.time, "sleep", lambda _s: None)
    assert anki_mod.ensure_anki_running(wait=5) is True
    assert calls["launched"][0] in ("open", "cmd", "anki")  # platform launch command


def test_ensure_returns_false_when_launch_fails(monkeypatch):
    monkeypatch.setattr(anki_mod, "anki_reachable", lambda *a, **k: False)

    def boom(*a, **k):
        raise OSError("no such app")

    monkeypatch.setattr(anki_mod.subprocess, "Popen", boom)
    assert anki_mod.ensure_anki_running(wait=1) is False  # warned + degraded, did not raise
