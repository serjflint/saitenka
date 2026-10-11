"""Lookahead index for whatever subtitle track is CURRENTLY SELECTED in mpv — embedded or external.

Prefetch (app/features/tooltip/prefetch.py) needs ``reader._sub_index`` to know upcoming lines. An external/jimaku
file already has a path to feed ``sub_index.load_index``. An EMBEDDED track (baked into the video
container, e.g. an .mkv's internal ass/srt) has none — extract it once via ffmpeg (mpv's track-list
``ff-index`` maps straight onto ffmpeg's ``-map 0:<n>``, confirmed against a real mpv 0.40 track-list)
and cache the result next to jimaku's own fetched-sub cache, keyed by video name+size+track+format so
a rewatch reuses it instead of re-extracting every session.

The extracted file is also what native-visible geometry reads as its authored source, so the
extraction format is not a private detail of the index: transcoding an embedded ASS track down to
SRT would leave the episode noninteractive. `subtitle_artifact.extract_spec` owns that choice.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.native_subtitles import NativeSubtitleGeometry

    PropertyGet = Callable[[str], object]

log = logging.getLogger(__name__)


def embedded_subs_cache_dir() -> Path:
    from saitenka.app.paths import cache_dir

    return cache_dir() / "embedded-subs"


def embedded_subs_cache_key(video: str | Path, ff_index: int, suffix: str) -> str:
    from saitenka.app.paths import sanitize_filename

    v = Path(video)
    try:
        size = v.stat().st_size
    except OSError:
        size = 0
    return sanitize_filename(f"{v.stem}-{size}-track{ff_index}") + suffix


def _selected_sub_track(ipc) -> dict | None:
    """The PRIMARY selected subtitle track-list entry (``main-selection`` 0), or None if no sub
    track is selected. mpv can have a secondary track selected too (dual-sub); only the primary
    one drives the overlay's lookahead."""
    data = ipc.query("track-list") or []
    subs = [t for t in data if t.get("type") == "sub" and t.get("selected")]
    if not subs:
        return None
    return min(subs, key=lambda t: t.get("main-selection", 0))


def extract_embedded_track(
    video: str | Path,
    ff_index: int,
    dest: Path,
    codec_args: tuple[str, ...],
    *,
    cancelled=None,
) -> bool:
    """ffmpeg-extract the embedded subtitle stream at ``ff_index`` to ``dest``. Fail-soft:
    logs and returns False on any ffmpeg/tool-missing error — a missing lookahead index is a
    cosmetic prefetch loss, never worth taking down the reader over."""
    from saitenka.mpvio.discover import find_tool

    exe = find_tool("ffmpeg") or "ffmpeg"  # GUI-launched mpv has a minimal PATH without Homebrew
    dest.parent.mkdir(parents=True, exist_ok=True)
    from saitenka.app.procutil import run_cancellable

    try:
        with tempfile.TemporaryDirectory(dir=dest.parent) as directory:
            staged = Path(directory) / dest.name
            run_cancellable(
                [exe, "-y", "-i", str(video), "-map", f"0:{ff_index}", *codec_args, str(staged)],
                check=True,
                capture_output=True,
                cancelled=cancelled,
            )
            if cancelled is not None and cancelled():
                return False
            staged.replace(dest)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("embedded sub-track extraction failed: %s", e)
        return False
    return True


def container_fonts_cache_dir() -> Path:
    from saitenka.app.paths import cache_dir

    return cache_dir() / "container-fonts"


def resolve_track_fonts(ipc, get: PropertyGet, geometry: NativeSubtitleGeometry) -> None:
    """Hand the geometry owner the same font set mpv's subtitle renderer holds for this track.

    Runs here because this is where a track load already pays for a subprocess and knows the media
    path; the attachments live in the video container even when the subtitle itself is external.
    Fail-soft: an unreachable mpv leaves the environment empty, which the geometry owner refuses
    rather than measures.
    """
    from saitenka.app import subtitle_fonts

    path = get("path")
    try:
        settings = {name: ipc.query(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS}
        environment = subtitle_fonts.resolve(
            expand=ipc.expand_path,
            settings=settings,
            video=Path(str(path)) if path else None,
            cache_dir=container_fonts_cache_dir(),
        )
    except (OSError, ValueError, TypeError) as error:
        log.warning("could not resolve the track's font sources: %s", error)
        return
    geometry.set_fonts(environment)
