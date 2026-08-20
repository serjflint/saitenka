"""Lookahead index for whatever subtitle track is CURRENTLY SELECTED in mpv — embedded or external.

Prefetch (app/prefetch.py) needs ``reader._sub_index`` to know upcoming lines. An external/jimaku
file already has a path to feed ``sub_index.load_index``. An EMBEDDED track (baked into the video
container, e.g. an .mkv's internal ass/srt) has none — extract it once via ffmpeg (mpv's track-list
``ff-index`` maps straight onto ffmpeg's ``-map 0:<n>``, confirmed against a real mpv 0.40 track-list)
and cache the result next to jimaku's own fetched-sub cache, keyed by video name+size+track so a
rewatch reuses it instead of re-extracting every session.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.app import subtitle_artifact

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.native_subtitles import NativeSubtitleGeometry

    PropertyGet = Callable[[str], object]

log = logging.getLogger(__name__)


def embedded_subs_cache_dir() -> Path:
    from saitenka.app.paths import cache_dir

    return cache_dir() / "embedded-subs"


def embedded_subs_cache_key(video: str | Path, ff_index: int) -> str:
    from saitenka.app.paths import sanitize_filename

    v = Path(video)
    try:
        size = v.stat().st_size
    except OSError:
        size = 0
    return sanitize_filename(f"{v.stem}-{size}-track{ff_index}") + ".srt"


def _selected_sub_track(ipc) -> dict | None:
    """The PRIMARY selected subtitle track-list entry (``main-selection`` 0), or None if no sub
    track is selected. mpv can have a secondary track selected too (dual-sub); only the primary
    one drives the overlay's lookahead."""
    data = ipc.command("get_property", "track-list").get("data") or []
    subs = [t for t in data if t.get("type") == "sub" and t.get("selected")]
    if not subs:
        return None
    return min(subs, key=lambda t: t.get("main-selection", 0))


def extract_embedded_track(video: str | Path, ff_index: int, dest: Path) -> bool:
    """ffmpeg-extract the embedded subtitle stream at ``ff_index`` to ``dest`` as .srt. Fail-soft:
    logs and returns False on any ffmpeg/tool-missing error — a missing lookahead index is a
    cosmetic prefetch loss, never worth taking down the reader over."""
    from saitenka.mpvio.discover import find_tool

    exe = find_tool("ffmpeg") or "ffmpeg"  # GUI-launched mpv has a minimal PATH without Homebrew
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [exe, "-y", "-i", str(video), "-map", f"0:{ff_index}", "-c:s", "srt", str(dest)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("embedded sub-track extraction failed: %s", e)
        return False
    return True


def build_sub_index_for_current_track(
    ipc, get: PropertyGet, load: Callable[[Path], None], geometry: NativeSubtitleGeometry | None
) -> None:
    """Populate the episode's cue index from whichever subtitle track mpv currently has selected.
    External/jimaku tracks (added via ``sub-add``, ``sub_file=`` or the jimaku fetch) already sit on
    disk — read the path straight off track-list's ``external-filename``. An embedded track is
    extracted once via ffmpeg and cached. Either way the result feeds the same
    ``sub_index.load_index`` parser that already powers Alt+←/→/↓ nav, so prefetch lookahead
    (app/prefetch.py's ``upcoming_cue_texts``) gets real upcoming lines regardless of subtitle
    source. Fail-soft throughout: no selected track / no video path / extraction failure just
    leaves the index unset."""
    if geometry is not None:
        geometry.set_source(None, live=True)
    artifact = subtitle_artifact.resolve(_selected_sub_track(ipc), media_path=get("path"))
    if isinstance(artifact, subtitle_artifact.ArtifactUnavailable):
        log.debug("no subtitle artifact for the current track: %s", artifact.value)
        return
    if isinstance(artifact, subtitle_artifact.ExternalArtifact):
        load(Path(artifact.path))
        return
    video_path = Path(artifact.media_path)
    dest = embedded_subs_cache_dir() / embedded_subs_cache_key(video_path, artifact.ff_index)
    if not dest.exists() and not extract_embedded_track(video_path, artifact.ff_index, dest):
        return
    load(dest)
