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

#: Slot modes. The plain slot ("") is what the providers fetched and resynced; `-raw` is a file the
#: user picked by hand (deliberately unsynced); `-retimed` is what "sync from here" made of either.
_RAW_MODE = "-raw"
_RETIMED_MODE = "-retimed"


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
    mode = "" if resync else _RAW_MODE
    return sanitize_filename(f"{video_path.stem}-{title}-ep{episode}-{size}{mode}")


def subs_cache_key(video: str | os.PathLike, title: str, episode, *, resync: bool = True) -> str:
    """The historical ``.srt``-suffixed slot filename — kept for the legacy ``jimaku/`` fallback lookup
    (those files were always ``.srt``). New writes carry the source's real extension; use :func:`_slot`
    for the extension-less name."""
    return _slot(video, title, episode, resync=resync) + ".srt"


def _slot_files(slot_dir: Path, slot: str) -> list[Path]:
    """Every cached subtitle for *slot*, best first — normally exactly one (one slot per
    (video, mode), evicted on write). ``glob.escape`` so a release group like ``[Erai]`` in the slot
    isn't read as a glob character class. A stored sub always has a SINGLE extension, so ``stem == slot``
    keeps ``<slot>.srt``/``<slot>.ass`` while excluding the multi-suffix bookkeeping resync leaves in the
    same dir (``<slot>.synced.srt``, its ``.synced`` marker, ``<slot>.win.srt``) — otherwise the newest
    of those (an empty marker) would shadow the real file. A pre-#237 flat ``<slot>.srt`` still matches,
    so old cache entries resolve with no migration.

    Ranked by `format_rank` before mtime — the SAME ranking the jimaku auto-pick uses, so a slot that
    kept both formats (eviction is best-effort: a sibling mpv still holds open survives the write)
    cannot serve the one a fresh fetch would have rejected.
    """
    matches = [p for p in slot_dir.glob(glob.escape(slot) + ".*") if p.is_file() and p.stem == slot]
    return sorted(matches, key=_rank, reverse=True)


def _rank(path: Path) -> tuple:
    from saitenka.app.subtitle_artifact import format_rank

    return (*format_rank(path.suffix), path.stat().st_mtime)


def cached_subs(
    video: str | os.PathLike, title: str, episode, *, resync: bool = True
) -> Path | None:
    """The best cached subtitle for this episode, or None.

    A resyncing session considers all three slots, because two of them hold work the user asked for
    by hand: ``-raw`` is what the source PICKER stored (deliberately unsynced), ``-retimed`` what
    "sync from here" produced. Reading only the auto-fetch slot made either last exactly one
    session — the next launch loaded whatever the providers had left there, so an `.ass` chosen on
    purpose lost to a months-old `.srt` every time.

    Ranked together by `_rank` — format first, then mtime — so this can neither serve a format a
    fresh fetch would have rejected, nor an older file than the correction made of it.
    """
    slot = _slot(video, title, episode, resync=resync)
    candidates = _slot_files(subs_cache_dir(), slot)
    if resync:
        for other in (_slot(video, title, episode, resync=False), slot + _RETIMED_MODE):
            candidates += _slot_files(subs_cache_dir(), other)
        candidates.sort(key=_rank, reverse=True)
    if candidates:
        return candidates[0]
    if not resync:
        return None
    from saitenka.app.paths import cache_dir

    # Default resync keys match the former Jimaku-only cache layout (always `.srt`).
    legacy = cache_dir() / "jimaku" / (slot + ".srt")
    return legacy if legacy.exists() else None


def publish_retimed(sub: Path, retimed: Path) -> Path | None:
    """Publish a re-time of a cached subtitle into this episode's ``-retimed`` slot.

    Its own slot, not one of the existing two, because all three mean different things and none may
    stand in for another: the plain slot is what the providers fetched and resynced, ``-raw`` is the
    pristine file the user picked by hand, ``-retimed`` is what "sync from here" made of one of them.
    Publishing over either would destroy an artifact the user may want back.

    Created if absent, overwritten if not, so pressing the key twice refines rather than accumulates.
    The lookup ranks all three together, so the re-time wins by being newest at equal format.

    Only applies to a file already IN the cache — a re-time of a ``--sub-file`` or a sibling next to
    the video has no slot to publish into and stays where it is. Returns the published path, or None
    when there was nothing to publish to.
    """
    if sub.parent != subs_cache_dir():
        return None
    slot = sub.stem.removesuffix(_RAW_MODE).removesuffix(_RETIMED_MODE) + _RETIMED_MODE
    destination = subs_cache_dir() / (slot + retimed.suffix.lower())
    for stale in _slot_files(subs_cache_dir(), slot):
        if stale != destination:
            try:
                stale.unlink()
            except OSError:
                log.debug("subtitle cache: could not evict %s", stale, exc_info=True)
    shutil.copy2(str(retimed), str(destination))
    log.info("subtitle cache: published re-timed %s", destination.name)
    return destination


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
