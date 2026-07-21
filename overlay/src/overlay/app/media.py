"""Card media: a clean video frame (mpv) + the subtitle's audio span (ffmpeg).

Screenshot uses mpv's ``screenshot-to-file … video`` so the card image is the raw frame — **not** our
OSD overlay. Audio is cut from the source file over the current subtitle's timespan (``sub-start`` /
``sub-end``), encoded mp3 with small fades, like animecards/mpvacious.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
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


def play_audio(path: str | Path) -> None:
    """Play a clip so the mined audio can be verified — non-blocking, no window."""
    if sys.platform == "darwin":
        cmd = ["afplay", str(path)]
    else:
        cmd = ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(path)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def speak(text: str, voice: str = "Kyoko") -> None:
    """Speak Japanese text via the OS TTS (macOS `say`, Windows SAPI) — non-blocking, no window."""
    if not text:
        return
    if sys.platform == "darwin":
        cmd = ["say", "-v", voice, text]
    elif sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak([Console]::In.ReadToEnd())"
        )
        try:
            p = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps], stdin=subprocess.PIPE
            )
            p.stdin.write(text.encode())
            p.stdin.close()
        except OSError:
            pass
        return
    else:
        cmd = ["espeak", "-v", "ja", text]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


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


def current_timespan(ipc) -> Timespan | None:
    """The current subtitle's [start, end] in file-timeline seconds, or None."""
    start = ipc.command("get_property", "sub-start").get("data")
    end = ipc.command("get_property", "sub-end").get("data")
    if start is None or end is None:
        return None
    return Timespan(float(start), float(end))
