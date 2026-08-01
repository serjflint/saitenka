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

from overlay.app.subtitle_modes import (
    lang_matches as _lang_matches,
)
from overlay.app.subtitle_modes import (
    select_initial,
)
from overlay.app.subtitle_modes import (
    sub_tracks as _sub_tracks,
)

log = logging.getLogger(__name__)


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


def fetch_jimaku_path(
    video: str,
    *,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
    resync: bool = True,
) -> tuple[Path | None, str]:
    """Fetch and optionally resync without touching mpv IPC, so callers may run it off-thread."""
    from overlay.app.jimaku import JimakuClient, JimakuError, parse_filename

    title, ep = parse_filename(video)
    title = jimaku_title or title
    ep = episode if episode is not None else ep
    tmp = tempfile.mkdtemp(prefix="saitenka-jimaku-")
    try:
        sub_path = JimakuClient(jimaku_key).fetch(title, ep, tmp)
    except JimakuError as e:
        return None, f"jimaku failed: {e}"
    if resync and Path(video).exists():
        from overlay.app.resync import maybe_resync

        sub_path = maybe_resync(Path(video), sub_path, enabled=True)
    return Path(sub_path), f"jimaku: added {Path(sub_path).name} for {title!r} ep {ep}"


def fetch_jimaku(
    ipc,
    *,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
    resync: bool = True,
) -> tuple[bool, str]:
    """Fetch JP subs from jimaku.cc for the attached mpv's current file, add + select them, and hide
    mpv's native rendering. Returns ``(ok, status)`` so callers can fall back on failure. Usable
    standalone as the runtime "force jimaku" action (a keybind can call this mid-playback)."""
    video = ipc.command("get_property", "path").get("data")
    if not video:
        return False, "jimaku: mpv reports no file path — cannot fetch"
    sub_path, status = fetch_jimaku_path(
        video,
        jimaku_key=jimaku_key,
        jimaku_title=jimaku_title,
        episode=episode,
        resync=resync,
    )
    if sub_path is None:
        return False, status
    _add_and_select(ipc, sub_path)
    ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format
    return True, status


def ensure_jp_subs(
    ipc,
    *,
    slang: str = "ja,jpn,jp",
    sub_file: str | None = None,
    jimaku: bool = False,
    jimaku_force: bool = False,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
    resync: bool = True,
) -> str:
    """Make Japanese subtitles active on an attached mpv, mirroring ``run``'s precedence:
    explicit file > existing JP track > jimaku fetch. ``jimaku_force`` flips jimaku AHEAD of the
    embedded track (for files whose baked-in JP subs are mistimed/wrong), falling back to the embedded
    track only if the fetch fails. Hides mpv's native sub rendering whenever it takes control. Returns
    a human-readable status line for the CLI to print."""
    if sub_file:
        _add_and_select(ipc, Path(sub_file).expanduser())
        ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format
        return f"using sub file {Path(sub_file).name}"

    if jimaku and jimaku_force:
        ok, status = fetch_jimaku(
            ipc,
            jimaku_key=jimaku_key,
            jimaku_title=jimaku_title,
            episode=episode,
            resync=resync,
        )
        if ok:
            return status
        log.warning("jimaku force fetch failed (%s) — falling back to the embedded track", status)

    sid = select_sub_track(ipc, slang)
    if sid is not None:
        ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format
        return f"selected JP subtitle track sid={sid}"

    if not jimaku:
        return "no Japanese subtitle track found (pass --jimaku to fetch, or --sub-file)"

    _, status = fetch_jimaku(
        ipc, jimaku_key=jimaku_key, jimaku_title=jimaku_title, episode=episode, resync=resync
    )
    return status


def prepare_attach_startup(
    ipc,
    *,
    slang: str = "ja,jpn,jp",
    sub_file: str | None = None,
    jimaku: bool = False,
    jimaku_force: bool = False,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
    resync: bool = True,
):
    """Select the immediate attach track and defer a missing-JP provider fetch."""
    status = ""
    if sub_file or jimaku_force:
        status = ensure_jp_subs(
            ipc,
            slang=slang,
            sub_file=sub_file,
            jimaku=jimaku,
            jimaku_force=jimaku_force,
            jimaku_key=jimaku_key,
            jimaku_title=jimaku_title,
            episode=episode,
            resync=resync,
        )

    startup = select_initial(ipc, slang)
    if not status:
        if startup.active == "jp":
            status = f"selected JP subtitle track sid={startup.tracks.jp_sid}"
        elif startup.active == "en":
            status = f"selected English fallback sid={startup.tracks.en_sid}"
        else:
            status = "no Japanese or English subtitle track found"
    return startup, status, jimaku and startup.tracks.jp_sid is None
