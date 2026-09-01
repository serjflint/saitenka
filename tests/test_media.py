"""OS TTS command construction — the cross-platform bit that actually broke on Windows."""

from __future__ import annotations

import base64

from saitenka_card import AnimatedClip

from saitenka.app import media


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


def test_tts_available_windows_needs_ja_voice(monkeypatch):
    media.tts_available.cache_clear()
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr(media, "_voices_out", lambda: "en-US\nja-JP\n")
    assert media.tts_available() is True
    media.tts_available.cache_clear()
    monkeypatch.setattr(media, "_voices_out", lambda: "en-US\nen-GB\n")
    assert media.tts_available() is False
    media.tts_available.cache_clear()


def test_tts_available_linux_needs_espeak(monkeypatch):
    media.tts_available.cache_clear()
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr(media.shutil, "which", lambda _n: "/usr/bin/espeak")
    assert media.tts_available() is True
    media.tts_available.cache_clear()
    monkeypatch.setattr(media.shutil, "which", lambda _n: None)
    assert media.tts_available() is False
    media.tts_available.cache_clear()


def test_speak_empty_is_noop(monkeypatch):
    calls: list = []
    monkeypatch.setattr(media.subprocess, "Popen", lambda *a, **_k: calls.append(a))
    media.speak("")
    assert calls == []  # empty text spawns nothing


def test_play_cmd_macos_uses_afplay(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "darwin")
    assert media._play_cmd("/tmp/a.m4a") == ["afplay", "/tmp/a.m4a"]


def test_play_cmd_windows_prefers_mpv_over_ffplay(monkeypatch):
    """The essentials ffmpeg build has no ffplay; mpv (a core dep) plays the clip headless instead."""
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr("saitenka.mpvio.discover.find_mpv", lambda _c: r"C:\mpv\mpv.exe")
    cmd = media._play_cmd("C:\\clip.m4a")
    assert cmd[0] == r"C:\mpv\mpv.exe" and "--no-video" in cmd and cmd[-1] == "C:\\clip.m4a"


def test_play_cmd_falls_back_to_ffplay_without_mpv(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr("saitenka.mpvio.discover.find_mpv", lambda _c: None)
    assert media._play_cmd("/tmp/a.m4a")[0] == "ffplay"


def test_speak_spawns_the_command(monkeypatch):
    calls: list = []
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "Popen", lambda cmd, **_k: calls.append(cmd))
    media.speak("ねこ", voice="Kyoko")
    assert calls == [["say", "-v", "Kyoko", "ねこ"]]


# --- animated screenshot (#92): encoder probe + ffmpeg command --------------------------------------


def _completed(stdout: str = "", returncode: int = 0):
    return media.subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def test_ffmpeg_encoder_available_probes_encoders_and_caches(monkeypatch):
    media._ffmpeg_encoder_available.cache_clear()
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    calls: list = []
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda cmd, **_k: (calls.append(cmd), _completed("V..... libwebp_anim"))[1],
    )
    assert media._ffmpeg_encoder_available("libwebp_anim") is True
    assert media._ffmpeg_encoder_available("libwebp_anim") is True  # served from cache
    assert len(calls) == 1  # probed once, not per call
    assert calls[0][0] == "ffmpeg" and "-encoders" in calls[0]
    media._ffmpeg_encoder_available.cache_clear()
    monkeypatch.setattr(media.subprocess, "run", lambda _cmd, **_k: _completed("V..... vp9"))
    assert media._ffmpeg_encoder_available("libwebp_anim") is False  # encoder not listed
    media._ffmpeg_encoder_available.cache_clear()


def test_ffmpeg_encoder_available_false_when_ffmpeg_missing(monkeypatch):
    media._ffmpeg_encoder_available.cache_clear()
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)

    def _raise(*_a, **_k):
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(media.subprocess, "run", _raise)
    assert media._ffmpeg_encoder_available("libwebp_anim") is False
    media._ffmpeg_encoder_available.cache_clear()


def test_animated_screenshot_builds_ffmpeg_over_the_capped_span(monkeypatch):
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    monkeypatch.setattr(media, "_ffmpeg_encoder_available", lambda _enc: True)
    calls: dict = {}
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **_k: calls.__setitem__("cmd", cmd))
    out = media.animated_screenshot(
        "/v.mkv",
        media.Timespan(10, 20),
        "/out.webp",
        AnimatedClip(fps=12, height=480, quality=75, max_secs=4.0),
    )
    assert out == media.Path("/out.webp")
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "libwebp_anim" in cmd and "-loop" in cmd and "-an" in cmd and "-sn" in cmd
    assert cmd[cmd.index("-vf") + 1] == "fps=12,scale=-2:480"  # the quality↔storage levers
    assert "9.500" in cmd  # padded start (10 - 0.5)
    assert "13.500" in cmd  # end capped at start + max_secs (9.5 + 4.0), not the full 20.5


def test_resolve_animated_encoder_prefers_webp_then_falls_back_to_gif(monkeypatch):
    have: set = set()
    monkeypatch.setattr(media, "_ffmpeg_encoder_available", lambda enc: enc in have)
    have = {"libwebp_anim", "gif"}
    assert media.resolve_animated_encoder("webp") == ("libwebp_anim", "webp")  # WebP preferred
    have = {"libwebp", "gif"}
    assert media.resolve_animated_encoder("webp") == ("libwebp", "webp")  # 2nd-choice WebP encoder
    have = {"gif"}  # the Homebrew-ffmpeg case: no libwebp → universal GIF fallback
    assert media.resolve_animated_encoder("webp") == ("gif", "gif")
    have = set()  # no ffmpeg encoders at all → caller keeps the still
    assert media.resolve_animated_encoder("webp") is None
    have = {"gif", "libwebp_anim"}
    assert (
        media.resolve_animated_encoder("av1") is None
    )  # explicit unsupported format, no gif fallback


def test_animated_screenshot_returns_none_without_any_encoder(monkeypatch):
    monkeypatch.setattr(media, "_ffmpeg_encoder_available", lambda _enc: False)
    ran: list = []
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **_k: ran.append(cmd))
    assert (
        media.animated_screenshot("/v.mkv", media.Timespan(0, 3), "/o.webp", AnimatedClip()) is None
    )
    assert ran == []  # no encode attempted when no encoder is available → caller keeps the still


def test_animated_screenshot_falls_back_to_gif_when_ffmpeg_lacks_webp(monkeypatch):
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    monkeypatch.setattr(media, "_ffmpeg_encoder_available", lambda enc: enc == "gif")
    calls: dict = {}
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **_k: calls.__setitem__("cmd", cmd))
    out = media.animated_screenshot("/v.mkv", media.Timespan(1, 3), "/out.webp", AnimatedClip())
    assert out == media.Path("/out.gif")  # extension swapped to the real (fallback) format
    cmd = calls["cmd"]
    assert "palettegen" in cmd[cmd.index("-vf") + 1]  # GIF uses the palette filtergraph
    assert not any("libwebp" in str(a) for a in cmd)


def test_animated_screenshot_unknown_format_is_none(monkeypatch):
    monkeypatch.setattr(media, "_ffmpeg_encoder_available", lambda _enc: True)
    # av1/mp4 needs a <video> template, out of scope → no encode, caller falls back to the still
    out = media.animated_screenshot(
        "/v.mkv", media.Timespan(0, 3), "/o.mp4", AnimatedClip(fmt="av1")
    )
    assert out is None
