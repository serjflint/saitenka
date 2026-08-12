"""Persistent cache for finished Japanese subtitles, independent of their provider."""

from __future__ import annotations

import glob
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os

log = logging.getLogger(__name__)


def subs_cache_dir() -> Path:
    from saitenka.app.paths import cache_dir

    return cache_dir() / "subtitles"


def _slot(video: str | os.PathLike, title: str, episode, *, resync: bool = True) -> str:
    """The extension-less cache slot for (video, title, episode, size, mode). The real download's
    extension is appended at store time (#237) — an ASS body no longer masquerades under ``.srt`` (the
    lie ``_alass_ready_source`` had to unpick at the aligner seam), and the file names what it holds.
    ``size`` invalidates the slot when the video is re-encoded under the same name."""
    from saitenka.app.paths import sanitize_filename

    video_path = Path(video)
    try:
        size = video_path.stat().st_size
    except OSError:
        size = 0
    mode = "" if resync else "-raw"
    return sanitize_filename(f"{video_path.stem}-{title}-ep{episode}-{size}{mode}")


def subs_cache_key(video: str | os.PathLike, title: str, episode, *, resync: bool = True) -> str:
    """The historical ``.srt``-suffixed slot filename — kept for the legacy ``jimaku/`` fallback lookup
    (those files were always ``.srt``). New writes carry the source's real extension; use :func:`_slot`
    for the extension-less name."""
    return _slot(video, title, episode, resync=resync) + ".srt"


def _slot_files(slot_dir: Path, slot: str) -> list[Path]:
    """Every cached subtitle for *slot*, newest-modified first — normally exactly one (one slot per
    (video, mode), evicted on write). ``glob.escape`` so a release group like ``[Erai]`` in the slot
    isn't read as a glob character class. A stored sub always has a SINGLE extension, so ``stem == slot``
    keeps ``<slot>.srt``/``<slot>.ass`` while excluding the multi-suffix bookkeeping resync leaves in the
    same dir (``<slot>.synced.srt``, its ``.synced`` marker, ``<slot>.win.srt``) — otherwise the newest
    of those (an empty marker) would shadow the real file. A pre-#237 flat ``<slot>.srt`` still matches,
    so old cache entries resolve with no migration."""
    matches = [p for p in slot_dir.glob(glob.escape(slot) + ".*") if p.is_file() and p.stem == slot]
    return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)


def cached_subs(
    video: str | os.PathLike, title: str, episode, *, resync: bool = True
) -> Path | None:
    slot = _slot(video, title, episode, resync=resync)
    if matches := _slot_files(subs_cache_dir(), slot):
        return matches[0]
    if not resync:
        return None
    from saitenka.app.paths import cache_dir

    # Default resync keys match the former Jimaku-only cache layout (always `.srt`).
    legacy = cache_dir() / "jimaku" / (slot + ".srt")
    return legacy if legacy.exists() else None


def store_subs(
    video: str | os.PathLike,
    title: str,
    episode,
    sub_path: str | os.PathLike,
    *,
    resync: bool = True,
) -> Path:
    destination_dir = subs_cache_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    slot = _slot(video, title, episode, resync=resync)
    suffix = Path(sub_path).suffix.lower() or ".srt"
    destination = destination_dir / (slot + suffix)
    # One slot per (video, mode): evict any stale sibling (e.g. a prior `.srt` when this write is `.ass`,
    # or vice-versa) so the glob lookup never returns two files for one logical slot. Best-effort — a
    # locked sibling (Windows: mpv still holds the prior file open) must not crash the fetch; the
    # newest-mtime lookup still returns this fresh write, so at worst the stale copy lingers unused.
    for stale in _slot_files(destination_dir, slot):
        if stale != destination:
            try:
                stale.unlink()
            except OSError:
                log.debug(
                    "subtitle cache: could not evict stale slot sibling %s", stale, exc_info=True
                )
    shutil.copy2(str(sub_path), str(destination))
    return destination
