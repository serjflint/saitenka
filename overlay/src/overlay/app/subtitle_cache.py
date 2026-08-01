"""Persistent cache for finished Japanese subtitles, independent of their provider."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os


def subs_cache_dir() -> Path:
    from overlay.app.paths import cache_dir

    return cache_dir() / "subtitles"


def subs_cache_key(video: str | os.PathLike, title: str, episode, *, resync: bool = True) -> str:
    from overlay.app.paths import sanitize_filename

    video_path = Path(video)
    try:
        size = video_path.stat().st_size
    except OSError:
        size = 0
    mode = "" if resync else "-raw"
    return sanitize_filename(f"{video_path.stem}-{title}-ep{episode}-{size}{mode}") + ".srt"


def cached_subs(
    video: str | os.PathLike, title: str, episode, *, resync: bool = True
) -> Path | None:
    key = subs_cache_key(video, title, episode, resync=resync)
    cached = subs_cache_dir() / key
    if cached.exists():
        return cached
    if not resync:
        return None
    from overlay.app.paths import cache_dir

    # Default resync keys match the former Jimaku-only cache layout.
    legacy = cache_dir() / "jimaku" / key
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
    destination = destination_dir / subs_cache_key(video, title, episode, resync=resync)
    shutil.copy2(str(sub_path), str(destination))
    return destination
