"""Subtitle-source resolution for attach/plugin mode.

``run`` launches its own mpv with ``--slang`` / ``--sub-file`` / jimaku, so it fully controls which
subtitle track is active. ``attach`` instead JOINS a user's mpv that may prefer English (mpv.conf
``slang=en``) or have auto-loaded junk externals (``sub-auto=all``) — so it must actively pick the
Japanese track over IPC, and optionally fetch jimaku when the file carries no JP subs at all. In
every case it hides mpv's own sub rendering, because the overlay draws its own.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _lang_matches(lang: str | None, wants: list[str]) -> bool:
    """True if an mpv track ``lang`` (e.g. ``jpn``/``ja``/``Japanese``) matches any wanted code.
    Prefix-both-ways so ``ja`` matches ``japanese`` and the 2-/3-letter ISO codes interoperate."""
    low = (lang or "").lower()
    return any(w and (low == w or low.startswith(w) or w.startswith(low)) for w in wants)


def _sub_tracks(ipc) -> list[dict]:
    data = ipc.command("get_property", "track-list").get("data") or []
    return [t for t in data if t.get("type") == "sub"]


def select_sub_track(ipc, slang: str) -> int | None:
    """Set mpv's active subtitle track (``sid``) to the first track whose language matches ``slang``
    in priority order. Returns the chosen sid, or None when no sub track matched."""
    wants = [s.strip().lower() for s in slang.split(",") if s.strip()]
    tracks = _sub_tracks(ipc)
    for want in wants:
        for t in tracks:
            if _lang_matches(t.get("lang"), [want]):
                sid = t.get("id")
                ipc.command("set_property", "sid", sid)
                return sid
    return None


def _add_and_select(ipc, sub_path: str | Path) -> None:
    ipc.command("sub-add", str(sub_path), "select")


def ensure_jp_subs(
    ipc,
    *,
    slang: str = "ja,jpn,jp",
    sub_file: str | None = None,
    jimaku: bool = False,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
    resync: bool = True,
) -> str:
    """Make Japanese subtitles active on an attached mpv, mirroring ``run``'s precedence:
    explicit file > existing JP track > jimaku fetch. Hides mpv's native sub rendering whenever it
    takes control. Returns a human-readable status line for the CLI to print."""
    if sub_file:
        _add_and_select(ipc, Path(sub_file).expanduser())
        ipc.command("set_property", "sub-visibility", False)
        return f"using sub file {Path(sub_file).name}"

    sid = select_sub_track(ipc, slang)
    if sid is not None:
        ipc.command("set_property", "sub-visibility", False)
        return f"selected JP subtitle track sid={sid}"

    if not jimaku:
        return "no Japanese subtitle track found (pass --jimaku to fetch, or --sub-file)"

    from overlay.app.jimaku import JimakuClient, JimakuError, parse_filename

    video = ipc.command("get_property", "path").get("data")
    if not video:
        return "jimaku: mpv reports no file path — cannot fetch"
    title, ep = parse_filename(video)
    title = jimaku_title or title
    ep = episode if episode is not None else ep
    tmp = tempfile.mkdtemp(prefix="saitenka-jimaku-")
    try:
        sub_path = JimakuClient(jimaku_key).fetch(title, ep, tmp)
    except JimakuError as e:
        return f"jimaku failed: {e}"
    if resync and Path(video).exists():
        from overlay.app.resync import maybe_resync

        sub_path = maybe_resync(Path(video), sub_path, enabled=True)
    _add_and_select(ipc, sub_path)
    ipc.command("set_property", "sub-visibility", False)
    return f"jimaku: added {Path(sub_path).name} for {title!r} ep {ep}"
