"""Episode identity + sibling-episode resolution (#100 watch continuity).

Filename parsing and title normalization are reused from jimaku/backlog so the
durable session store (#24), the deferred-capture backlog (#64), and post-playback
continuity all agree on `(title_match, episode)`.
"""

from __future__ import annotations

from pathlib import Path

from saitenka.app.backlog import normalize_title
from saitenka.app.jimaku import parse_filename

# The video containers Saitenka plays; the sibling scan ignores everything else
# (subtitles, images, .part downloads) so a stray file can't shadow an episode.
VIDEO_SUFFIXES = frozenset(
    {".mkv", ".mp4", ".webm", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".flv", ".mpg", ".mpeg"}
)


def episode_identity(path: str | Path) -> tuple[str, str, int | None]:
    """`(title, title_match, episode)` for a media filename; `title_match` agrees with backlog.media."""
    title, episode = parse_filename(path)
    return title, normalize_title(title), episode


def resolve_sibling(path: str | Path, delta: int) -> Path | None:
    """The episode `delta` away from `path` in the same directory, or `None`.

    Non-destructive by design: an unparseable name, an out-of-range target, no
    matching sibling, or an ambiguous match (anything other than exactly one)
    all yield `None`. Directory-scan only — no store, no side effects — so
    auto-advance can drive it without touching the backlog DB.
    """
    p = Path(path)
    _, title_match, episode = episode_identity(p.name)
    if episode is None:
        return None
    want = episode + delta
    if want < 0:
        return None
    try:
        entries = list(p.parent.iterdir())
    except OSError:
        return None
    matches = [
        f
        for f in entries
        if f.name != p.name
        and f.suffix.lower() in VIDEO_SUFFIXES
        and episode_identity(f.name)[1:] == (title_match, want)
    ]
    return matches[0] if len(matches) == 1 else None
