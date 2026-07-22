"""Card media: a clean video frame (mpv) + the subtitle's audio span (ffmpeg).

Screenshot uses mpv's ``screenshot-to-file … video`` so the card image is the raw frame — **not** our
OSD overlay. Audio is cut from the source file over the current subtitle's timespan (``sub-start`` /
``sub-end``), encoded mp3 with small fades, like animecards/mpvacious.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class Timespan:
    start: float
    end: float

    def padded(self, pad: float) -> Timespan:
        return Timespan(max(0.0, self.start - pad), self.end + pad)

    @property
    def duration(self) -> float:
        return max(0.05, self.end - self.start)


def screenshot(ipc, path: str | Path) -> Path:
    """Save the current frame (video only — no subs/OSD) via mpv."""
    ipc.command("screenshot-to-file", str(path), "video")
    return Path(path)


def clip_audio(
    video: str | Path,
    span: Timespan,
    path: str | Path,
    pad: float = 0.5,
    fade: float = 0.1,
    track: int = 0,
) -> Path:
    """Extract [start-pad, end+pad] of audio track `track` as mono AAC (.m4a) with fades.

    AAC is ffmpeg's built-in encoder (no libmp3lame dependency) and plays on every current Anki
    client. Pass an ``.m4a`` output path so the container matches the codec."""
    p = span.padded(pad)
    dur = p.duration
    af = f"afade=t=in:st=0:d={fade},afade=t=out:st={max(0.0, dur - fade):.3f}:d={fade}"
    from overlay.mpvio.discover import find_tool

    cmd = [
        find_tool("ffmpeg") or "ffmpeg",  # GUI-launched mpv has a minimal PATH without Homebrew
        "-y",
        "-ss",
        f"{p.start:.3f}",
        "-to",
        f"{p.end:.3f}",
        "-i",
        str(video),
        "-map",
        f"0:a:{track}",
        "-af",
        af,
        "-ac",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return Path(path)


def _play_cmd(path: str | Path) -> list[str]:
    """The command to play a clip. macOS uses ``afplay``; elsewhere prefer **mpv** — it's a guaranteed
    core dependency, whereas ``ffplay`` is absent from the common Windows ffmpeg "essentials" build
    (gyan.dev), so the old ffplay-only path silently no-op'd there. ``ffplay`` is the last resort."""
    if sys.platform == "darwin":
        return ["afplay", str(path)]
    else:  # explicit else so mypy treats this as the inactive platform branch, not unreachable code
        from overlay.mpvio.discover import find_mpv

        mpv = find_mpv(None)
        if mpv:
            return [mpv, "--no-video", "--no-terminal", "--really-quiet", str(path)]
        return ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(path)]


def play_audio(path: str | Path) -> None:
    """Play a clip so the mined audio can be verified — non-blocking, no window."""
    try:
        subprocess.Popen(_play_cmd(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _speak_cmd(text: str, voice: str = "Kyoko") -> list[str]:
    """The OS TTS command for ``text`` (macOS ``say``, Windows SAPI via PowerShell, Linux ``espeak``).

    Windows carries the text as **base64 UTF-8 embedded in the script** — NOT piped via stdin: PowerShell
    decodes stdin with the console's OEM input codepage, so Japanese UTF-8 bytes arrived as mojibake and
    SAPI spoke nothing. It also selects an installed **Japanese** SAPI voice (Haruka) when present — the
    default voice is English and can't pronounce kana. (if/elif/else, not early-return, so mypy's
    sys.platform narrowing doesn't flag the other branches as unreachable.)"""
    if sys.platform == "win32":
        import base64

        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            f"$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}'));"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$v=$s.GetInstalledVoices()|"
            "?{$_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'ja-JP'}|select -First 1;"
            "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
            "$s.Speak($t)"
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
    elif sys.platform == "darwin":
        return ["say", "-v", voice, text]
    else:
        return ["espeak", "-v", "ja", text]


def speak(text: str, voice: str = "Kyoko") -> None:
    """Speak Japanese text via the OS TTS — non-blocking, no window. No-op on empty text or when the
    TTS binary is missing."""
    if not text:
        return
    try:
        subprocess.Popen(
            _speak_cmd(text, voice), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        pass


def _voices_out() -> str:
    """Raw list of installed TTS voices (best-effort ''): SAPI cultures on Windows, ``say -v ?`` on
    macOS. Its own function so tests can stub it without a real TTS engine."""
    if sys.platform == "win32":
        return _run_out(
            "powershell",
            "-NoProfile",
            "-Command",
            "Add-Type -AssemblyName System.Speech;"
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()|"
            "%{$_.VoiceInfo.Culture.Name}",
        )
    elif sys.platform == "darwin":
        return _run_out("say", "-v", "?")
    else:
        return ""


def _run_out(*args: str) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


@lru_cache(maxsize=1)
def tts_available() -> bool:
    """True if the OS has a voice the 🔊 button can use to read **Japanese** — a Japanese SAPI voice on
    Windows, a ``ja_JP`` ``say`` voice on macOS, or ``espeak`` on Linux. Cached (voices don't change
    mid-session). Used both by doctor and to HIDE the 🔊 button when it would silently do nothing."""
    if sys.platform == "win32":
        return "ja-JP" in _voices_out()
    elif sys.platform == "darwin":
        return bool(re.search(r"ja_JP", _voices_out()))
    else:
        return shutil.which("espeak") is not None


def copy_clipboard(text: str) -> None:
    """Put text on the system clipboard (macOS pbcopy / Windows clip)."""
    cmd = (
        ["pbcopy"]
        if sys.platform == "darwin"
        else (["clip"] if sys.platform == "win32" else ["xclip", "-selection", "clipboard"])
    )
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        p.communicate(text.encode("utf-8"))
    except OSError:
        pass


def audio_duration(path: str | Path) -> float | None:
    from overlay.mpvio.discover import find_tool

    try:
        out = subprocess.run(
            [
                find_tool("ffprobe") or "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nk=1:nw=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError):
        return None


def has_sub_lang(path: str | Path, langs: str = "ja,jpn,jp") -> bool | None:
    """True if the file carries a SUBTITLE stream tagged with one of ``langs`` (comma-sep), False if
    not, ``None`` if we couldn't probe (ffprobe missing / unreadable). ``run`` uses this to auto-fetch
    jimaku ONLY when a file has no embedded JP subs (matching what ``attach`` does over IPC)."""
    from overlay.mpvio.discover import find_tool

    wanted = {s.strip().lower() for s in langs.split(",") if s.strip()}
    try:
        out = subprocess.run(
            [
                find_tool("ffprobe") or "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream_tags=language",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    found = {line.strip().lower() for line in out.stdout.splitlines() if line.strip()}
    return bool(found & wanted)


def current_timespan(ipc) -> Timespan | None:
    """The current subtitle's [start, end] in file-timeline seconds, or None."""
    start = ipc.command("get_property", "sub-start").get("data")
    end = ipc.command("get_property", "sub-end").get("data")
    if start is None or end is None:
        return None
    return Timespan(float(start), float(end))
