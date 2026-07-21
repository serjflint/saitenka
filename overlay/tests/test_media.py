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


def test_play_cmd_macos_uses_afplay(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "darwin")
    assert media._play_cmd("/tmp/a.m4a") == ["afplay", "/tmp/a.m4a"]


def test_play_cmd_windows_prefers_mpv_over_ffplay(monkeypatch):
    """The essentials ffmpeg build has no ffplay; mpv (a core dep) plays the clip headless instead."""
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr("overlay.mpvio.discover.find_mpv", lambda _c: r"C:\mpv\mpv.exe")
    cmd = media._play_cmd("C:\\clip.m4a")
    assert cmd[0] == r"C:\mpv\mpv.exe" and "--no-video" in cmd and cmd[-1] == "C:\\clip.m4a"


def test_play_cmd_falls_back_to_ffplay_without_mpv(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr("overlay.mpvio.discover.find_mpv", lambda _c: None)
    assert media._play_cmd("/tmp/a.m4a")[0] == "ffplay"


def test_speak_spawns_the_command(monkeypatch):
    calls: list = []
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    media.speak("ねこ", voice="Kyoko")
    assert calls == [["say", "-v", "Kyoko", "ねこ"]]
