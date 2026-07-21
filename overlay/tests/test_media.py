"""OS TTS command construction — the cross-platform bit that actually broke on Windows."""

from __future__ import annotations

import base64

from overlay.app import media


def test_speak_cmd_macos(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "darwin")
    assert media._speak_cmd("猫", voice="Kyoko") == ["say", "-v", "Kyoko", "猫"]


def test_speak_cmd_windows_carries_utf8_as_base64_and_picks_ja_voice(monkeypatch):
    """Regression: Japanese piped via stdin was decoded with the console codepage → mojibake → silence.
    The command must embed the text as base64 UTF-8 and select a Japanese SAPI voice."""
    monkeypatch.setattr(media.sys, "platform", "win32")
    cmd = media._speak_cmd("猫")
    assert cmd[0] == "powershell"
    ps = cmd[-1]
    assert base64.b64encode("猫".encode()).decode("ascii") in ps  # UTF-8 carried losslessly
    assert "FromBase64String" in ps and "System.Speech" in ps
    assert "ja-JP" in ps  # selects an installed Japanese voice
    assert "[Console]::In" not in ps  # no fragile stdin path


def test_speak_cmd_linux(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "linux")
    assert media._speak_cmd("猫") == ["espeak", "-v", "ja", "猫"]


def test_speak_empty_is_noop(monkeypatch):
    calls: list = []
    monkeypatch.setattr(media.subprocess, "Popen", lambda *a, **k: calls.append(a))
    media.speak("")
    assert calls == []  # empty text spawns nothing


def test_speak_spawns_the_command(monkeypatch):
    calls: list = []
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    media.speak("ねこ", voice="Kyoko")
    assert calls == [["say", "-v", "Kyoko", "ねこ"]]
