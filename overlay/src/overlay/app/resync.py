"""Subtitle resync via alass (preferred) or ffsubsync (fallback via uvx).

Flow:
  1. Check for a ``<out>.synced`` marker — if present the file was already synced, skip.
  2. Try ``alass`` (on PATH); if absent try ``uvx ffsubsync``.
  3. On success, write the marker so the next run skips the tool.
  4. On failure (non-zero exit, timeout, tool absent) raise the appropriate exception so the
     caller can toast + fall back gracefully.

The public entry point for the jimaku path is :func:`maybe_resync` — it swallows
:exc:`ResyncUnavailable` and :exc:`ResyncFailed` and returns the original path, matching the
"graceful fallback" requirement.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Default timeout for the resync subprocess (seconds). Long enough for feature-length subs.
_TIMEOUT = 300


class ResyncUnavailable(RuntimeError):
    """Neither alass nor uvx is on PATH — resync cannot be performed."""


class ResyncFailed(RuntimeError):
    """The resync tool ran but returned a non-zero exit code or timed out."""


def _marker(out: Path) -> Path:
    """Path of the cache marker for ``out``."""
    return out.with_suffix(out.suffix + ".synced")


def resync(video: Path, src: Path, out: Path, *, timeout: int = _TIMEOUT) -> Path:
    """Synchronise ``src`` to ``video`` and write the result to ``out``.

    Uses ``alass`` if on PATH, else ``uvx ffsubsync``.  Writes a ``<out>.synced``
    marker on success so subsequent calls are no-ops.

    Parameters
    ----------
    video:
        The reference video file (for audio-based sync).
    src:
        The subtitle file to resync.
    out:
        Destination path for the resynced subtitle.
    timeout:
        Maximum seconds to wait for the subprocess (default 300).

    Returns
    -------
    Path
        ``out`` (the resynced subtitle path).

    Raises
    ------
    ResyncUnavailable
        Neither ``alass`` nor ``uvx`` is found on PATH.
    ResyncFailed
        The tool exited with a non-zero code or timed out.
    """
    marker = _marker(out)
    if marker.exists() and out.exists():
        log.debug("resync: cache hit for %s — skipping", out.name)
        return out

    if shutil.which("alass"):
        cmd = ["alass", str(video), str(src), str(out)]
        tool = "alass"
    elif shutil.which("uvx"):
        cmd = ["uvx", "ffsubsync", str(video), "-i", str(src), "-o", str(out)]
        tool = "uvx ffsubsync"
    else:
        raise ResyncUnavailable(
            "subtitle resync requires alass or ffsubsync; install alass (brew install alass)"
            " or ensure uvx is on PATH"
        )

    log.debug("resync: running %s on %s", tool, src.name)
    try:
        result = subprocess.run(cmd, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise ResyncFailed(f"resync timed out after {timeout}s ({tool})") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if result.stderr else ""
        raise ResyncFailed(f"resync failed (exit {result.returncode}) via {tool}: {stderr[:200]}")

    marker.touch()
    log.debug("resync: wrote %s", out.name)
    return out


def maybe_resync(
    video: Path,
    src: Path,
    *,
    enabled: bool = True,
    timeout: int = _TIMEOUT,
) -> Path:
    """Resync ``src`` to ``video`` if *enabled*, returning the synced path.

    Swallows :exc:`ResyncUnavailable` and :exc:`ResyncFailed` — in both cases the original
    ``src`` is returned so the caller can proceed with unsynced subtitles.  A warning is
    logged so the issue is visible in the rotating log without crashing the overlay.

    The output path is placed next to ``src`` with the stem extended by ``.synced``
    (e.g. ``ep01.srt`` → ``ep01.synced.srt``).
    """
    if not enabled:
        return src

    out = src.with_name(src.stem + ".synced" + src.suffix)
    try:
        return resync(video, src, out, timeout=timeout)
    except ResyncUnavailable as exc:
        log.warning("subtitle resync unavailable — using original: %s", exc)
        return src
    except ResyncFailed as exc:
        log.warning("subtitle resync failed — using original: %s", exc)
        return src
