"""Card media: a clean video frame (mpv) + the subtitle's audio span (ffmpeg).

Screenshot uses mpv's ``screenshot-to-file … video`` so the card image is the raw frame — **not** our
OSD saitenka. Audio is cut from the source file over the current subtitle's timespan (``sub-start`` /
``sub-end``), encoded mp3 with small fades, like animecards/mpvacious.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka_card import AnimatedClip


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


# EBU R128 target for opt-in loudness normalization: broadcast −23 LUFS, −1.5 dBTP ceiling, 11 LU
# range. Single-pass (measure+correct in one ffmpeg run) — two-pass would double mining latency for a
# few-second clip and buys little on speech.
_LOUDNORM = "loudnorm=I=-23:TP=-1.5:LRA=11"


def clip_audio(
    video: str | Path,
    span: Timespan,
    path: str | Path,
    pad: float = 0.5,
    fade: float = 0.1,
    track: int = 0,
    *,
    normalize: bool = False,
) -> Path:
    """Extract [start-pad, end+pad] of audio track `track` as mono AAC (.m4a) with fades.

    AAC is ffmpeg's built-in encoder (no libmp3lame dependency) and plays on every current Anki
    client. Pass an ``.m4a`` output path so the container matches the codec. ``normalize`` prepends an
    EBU R128 ``loudnorm`` pass (:data:`_LOUDNORM`) so card-to-card playback loudness is even — before
    the fades, so loudnorm measures the raw span, not the faded-out tails."""
    p = span.padded(pad)
    dur = p.duration
    fades = f"afade=t=in:st=0:d={fade},afade=t=out:st={max(0.0, dur - fade):.3f}:d={fade}"
    af = f"{_LOUDNORM},{fades}" if normalize else fades
    from saitenka.mpvio.discover import find_tool

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


# WebP animated encoders, best-quality first. Both produce a small animated .webp that drops into the
# card's existing `<img src>` Picture field. Many ffmpeg builds ship WITHOUT libwebp (e.g. Homebrew's
# ffmpeg 8, the Windows "essentials" build), so GIF is the universal fallback below.
_WEBP_ENCODERS = ("libwebp_anim", "libwebp")


@cache
def _ffmpeg_encoder_available(encoder: str) -> bool:
    """True if the local ffmpeg build ships ``encoder`` (``libwebp_anim``/``libwebp`` for WebP, ``gif`` for
    GIF). Cached — the ffmpeg binary doesn't change mid-session (tests call ``.cache_clear()``). Mirrors
    :func:`saitenka.app.doctor.check_ffmpeg`'s ``-encoders`` probe."""
    from saitenka.mpvio.discover import find_tool

    try:
        r = subprocess.run(
            [find_tool("ffmpeg") or "ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and encoder in r.stdout


def resolve_animated_encoder(fmt: str) -> tuple[str, str] | None:
    """``(ffmpeg encoder, file extension)`` for ``fmt`` given what the local ffmpeg has — or ``None`` when
    even the universal GIF encoder is missing (no ffmpeg at all). ``"webp"`` prefers WebP then falls back
    to GIF; ``"gif"`` forces GIF. GIF's encoder is native to every ffmpeg build, so animation works out of
    the box even where libwebp is absent — an explicit unsupported ``fmt`` (av1/mp4) returns ``None``."""
    if fmt == "webp":
        for enc in _WEBP_ENCODERS:
            if _ffmpeg_encoder_available(enc):
                return enc, "webp"
    if fmt in {"webp", "gif"} and _ffmpeg_encoder_available("gif"):
        return "gif", "gif"
    return None


def _animated_cmd(
    ffmpeg: str, start: float, end: float, video, encoder: str, opts, out: Path
) -> list:
    common = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        str(video),
        "-an",
        "-sn",
    ]
    scale = f"fps={opts.fps},scale=-2:{opts.height}"
    if encoder == "gif":
        # palettegen/paletteuse → a decent palette instead of gif's ugly default dithering
        vf = f"{scale}:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
        return [*common, "-vf", vf, "-loop", "0", str(out)]
    return [
        *common,
        "-vf",
        scale,
        "-c:v",
        encoder,
        "-loop",
        "0",
        "-quality",
        str(opts.quality),
        str(out),
    ]


def animated_screenshot(
    video: str | Path, span: Timespan, path: str | Path, opts: AnimatedClip, *, pad: float = 0.5
) -> Path | None:
    """Encode the cue span as a short animated clip from the RAW ``video`` — a motion screenshot for the
    card. Prefers WebP, falls back to a universal GIF (:func:`resolve_animated_encoder`); returns the
    actual output path (its extension matches the chosen format), or ``None`` when no encoder is available
    (the caller keeps the mpv still). The passed ``path``'s suffix is replaced with the real one.

    Same source + span as :func:`clip_audio` (the raw ``video`` layer, so no subtitle/OSD burn-in). The
    ``opts`` height/fps/quality are the quality↔storage levers; ``opts.max_secs`` bounds the clip so a long
    cue can't produce a huge file. Runs with a timeout so a stuck encode can't hang a mine."""
    resolved = resolve_animated_encoder(opts.fmt)
    if resolved is None:
        return None
    encoder, ext = resolved
    from saitenka.mpvio.discover import find_tool

    out = Path(path).with_suffix(f".{ext}")
    p = span.padded(pad)
    end = min(p.start + opts.max_secs, p.end)
    cmd = _animated_cmd(find_tool("ffmpeg") or "ffmpeg", p.start, end, video, encoder, opts, out)
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return out


def _play_cmd(path: str | Path) -> list[str]:
    """The command to play a clip. macOS uses ``afplay``; elsewhere prefer **mpv** — it's a guaranteed
    core dependency, whereas ``ffplay`` is absent from the common Windows ffmpeg "essentials" build
    (gyan.dev), so the old ffplay-only path silently no-op'd there. ``ffplay`` is the last resort."""
    if sys.platform == "darwin":
        return ["afplay", str(path)]
    else:  # explicit else so mypy treats this as the inactive platform branch, not unreachable code
        from saitenka.mpvio.discover import find_mpv

        mpv = find_mpv(None)
        if mpv:
            return [mpv, "--no-video", "--no-terminal", "--really-quiet", str(path)]
        return ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(path)]


def play_audio(path: str | Path) -> subprocess.Popen | None:
    """Play a clip so the mined audio can be verified — non-blocking, no window. Returns the player
    handle so a caller can stop it on preview dismiss (the clip outlives the panel otherwise); ``None``
    if the player couldn't launch."""
    try:
        return subprocess.Popen(
            _play_cmd(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        return None


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
        r = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=10, check=False
        )
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
        return bool(r"ja_JP" in _voices_out())
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
    from saitenka.mpvio.discover import find_tool

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
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError):
        return None


def has_sub_lang(path: str | Path, langs: str = "ja,jpn,jp") -> bool | None:
    """True if the file carries a SUBTITLE stream tagged with one of ``langs`` (comma-sep), False if
    not, ``None`` if we couldn't probe (ffprobe missing / unreadable). ``run`` uses this to auto-fetch
    jimaku ONLY when a file has no embedded JP subs (matching what ``attach`` does over IPC)."""
    from saitenka.mpvio.discover import find_tool

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
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    found = {line.strip().lower() for line in out.stdout.splitlines() if line.strip()}
    return bool(found & wanted)


def current_timespan(ipc) -> Timespan | None:
    """The current subtitle's [start, end] in file-timeline seconds, or None."""
    start = ipc.query("sub-start")
    end = ipc.query("sub-end")
    if start is None or end is None:
        return None
    return Timespan(float(start), float(end))
