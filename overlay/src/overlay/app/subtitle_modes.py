"""Primary JP/EN subtitle selection and non-destructive background track arrival."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from overlay.app.languages import MAIN_LANG, SECOND_LANG, Language

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from overlay.app.controller import Reader

    ProviderFetch = Callable[[], tuple[Path | None, str]]
    ProviderFetchFactory = Callable[[str], ProviderFetch]

log = logging.getLogger(__name__)

EN_LANGS = {"en", "eng", "en-us", "en-gb", "eng-us", "english"}
JP_LANGS = {"ja", "jpn", "jp", "japanese"}


def lang_matches(lang: str | None, wants: list[str]) -> bool:
    low = (lang or "").lower()
    return any(
        want and (low == want or low.startswith(want) or want.startswith(low)) for want in wants
    )


def sub_tracks(ipc) -> list[dict]:
    data = ipc.command("get_property", "track-list").get("data") or []
    return [track for track in data if track.get("type") == "sub"]


def _matching_track(tracks: list[dict], wants: list[str]) -> dict | None:
    return next((track for track in tracks if lang_matches(track.get("lang"), wants)), None)


def _fill_untagged_tracks(
    tracks: list[dict], jp: dict | None, en: dict | None
) -> tuple[dict | None, dict | None]:
    selected = sorted(
        (track for track in tracks if track.get("selected")),
        key=lambda track: track.get("main-selection", 0),
    )
    if jp is None and selected and selected[0] is not en:
        jp = selected[0]
    if jp is None and en is None and tracks:
        jp = tracks[0]
    if en is None:
        en = next((track for track in tracks if track.get("id") != (jp or {}).get("id")), None)
    return jp, en


@dataclass(frozen=True)
class SubtitleTracks:
    jp_sid: int | None
    en_sid: int | None


@dataclass(frozen=True)
class SubtitleStartup:
    tracks: SubtitleTracks
    active: Language | None


@dataclass(frozen=True)
class SubtitleFetchResult:
    path: Path | None
    status: str
    select_if_unchanged: bool
    initial_sid: int | str | None
    replace: bool = (
        False  # a user retry: swap the on-screen (mistimed) JP for the fresh re-synced one
    )
    force_select: bool = (
        False  # an explicit picker choice: select the fetched track NOW, even from English
    )


def discover_tracks(ipc, slang: str = "ja,jpn,jp") -> SubtitleTracks:
    tracks = sub_tracks(ipc)
    wants = [part.strip().lower() for part in slang.split(",") if part.strip()]
    jp = _matching_track(tracks, wants)
    en = _matching_track(tracks, list(EN_LANGS))
    jp, en = _fill_untagged_tracks(tracks, jp, en)
    return SubtitleTracks(
        jp_sid=jp.get("id") if jp is not None else None,
        en_sid=en.get("id") if en is not None else None,
    )


def select_initial(ipc, slang: str = "ja,jpn,jp") -> SubtitleStartup:
    """Prefer Japanese, fall back to tagged English, and leave a missing-both file untouched."""
    tracks = discover_tracks(ipc, slang)
    active: Language | None = None
    sid = tracks.jp_sid
    if sid is not None:
        active = MAIN_LANG
    elif tracks.en_sid is not None:
        active, sid = SECOND_LANG, tracks.en_sid
    if sid is not None:
        ipc.command("set_property", "sid", sid)
        ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC wire value
    return SubtitleStartup(tracks, active)


def configure(reader: Reader, startup: SubtitleStartup, *, slang: str = "ja,jpn,jp") -> None:
    reader.jp_sid = startup.tracks.jp_sid
    reader.en_sid = startup.tracks.en_sid
    reader.subtitle_language = startup.active or MAIN_LANG
    reader.subtitle_slang = slang
    if reader._get("secondary-sid") not in {None, False, "no"}:
        reader.ipc.command("set_property", "secondary-sid", "no")
    from overlay.app import analysis_overlay

    analysis_overlay.on_index_changed(reader)


def setup_secondary(reader: Reader) -> int | None:
    if reader.jp_sid is None and reader.en_sid is None:
        tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
        reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    sid = reader.en_sid if reader.subtitle_language == MAIN_LANG else reader.jp_sid
    if sid is None or sid == reader._get("sid"):
        release_secondary(reader)
        return None
    if reader._translation_secondary_sid == sid:
        return sid
    reader.ipc.command("set_property", "secondary-sid", sid)
    reader.ipc.command("set_property", "secondary-sub-visibility", False)  # noqa: FBT003  # mpv IPC wire value
    reader._translation_secondary_sid = sid
    return sid


def release_secondary(reader: Reader) -> None:
    if reader._translation_secondary_sid is None:
        return
    reader.ipc.command("set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None


def on_primary_changed(reader: Reader, sid) -> None:
    if sid == reader._translation_secondary_sid:
        return
    announce_track(reader, sid)
    if sid == reader.jp_sid:
        language: Language = MAIN_LANG
    elif sid == reader.en_sid:
        language = SECOND_LANG
    else:
        return
    if language != reader.subtitle_language:
        reader.subtitle_language = language
        reader._sub_index = None
        from overlay.app import analysis_overlay

        analysis_overlay.on_index_changed(reader)
    if reader._translation_visible():
        setup_secondary(reader)
    else:
        release_secondary(reader)


def _language_name(lang: str | None) -> str:
    low = (lang or "").lower()
    if low in JP_LANGS:
        return "Japanese"
    if low in EN_LANGS:
        return "English"
    return lang or "unknown language"


def announce_track(reader: Reader, sid) -> None:
    if sid == reader._last_announced_sid:
        return
    tracks = sub_tracks(reader.ipc)
    for index, track in enumerate(tracks, 1):
        if track.get("id") == sid:
            reader._last_announced_sid = sid
            name = _language_name(track.get("lang"))
            # Log the same signal the toast shows: a surprising "unknown language (10/11)" here is the
            # earliest sign of a wrong-track selection, and belongs in the bundle, not just on screen.
            log.info("subtitles announced: %s (%d/%d) sid=%s", name, index, len(tracks), sid)
            reader._toast(f"subtitles: {name} ({index}/{len(tracks)})")
            return


def toggle(reader: Reader) -> None:
    tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
    reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    active_sid = reader._get("sid")
    if active_sid == reader.jp_sid:
        target: Language = SECOND_LANG
    elif active_sid == reader.en_sid or (
        reader.subtitle_language == MAIN_LANG and reader.jp_sid is not None
    ):
        target = MAIN_LANG
    elif reader.subtitle_language == SECOND_LANG and reader.en_sid is not None:
        target = SECOND_LANG
    else:
        target = MAIN_LANG if reader.jp_sid is not None else SECOND_LANG
    sid = reader.en_sid if target == SECOND_LANG else reader.jp_sid
    if sid is None:
        reader._toast(f"{target.upper()} subtitles unavailable", "warn")
        return

    reader.ipc.command("set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None
    reader.ipc.command("set_property", "sid", sid)
    reader.subtitle_language = target
    reader._sub_index = None
    from overlay.app import analysis_overlay

    analysis_overlay.on_index_changed(reader)
    reader.set_subtitle("")
    if reader._translation_visible():
        setup_secondary(reader)
    else:
        release_secondary(reader)
    from overlay.app.embedded_subs import build_sub_index_for_current_track

    build_sub_index_for_current_track(reader)
    announce_track(reader, sid)


def start_fetch(
    reader: Reader,
    fetch: ProviderFetch,
    *,
    name: str = "sub-provider",
    select_if_unchanged: bool = False,
    replace: bool = False,
    force_select: bool = False,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Run provider I/O off-thread; mpv IPC stays on the reader thread. ``replace`` (a user retry)
    swaps the current on-screen JP track for the freshly fetched/re-synced one; the background path
    leaves ``replace`` false so it never disrupts what you're watching. ``force_select`` (the picker's
    explicit choice) selects the fetched track NOW even from English, overriding the keep-current
    background contract."""
    initial_sid = reader._get("sid") if select_if_unchanged else None

    def work() -> None:
        try:
            try:
                path, status = fetch()
                reader._subtitle_results.put(
                    SubtitleFetchResult(
                        path, status, select_if_unchanged, initial_sid, replace, force_select
                    )
                )
            except (
                Exception
            ) as exc:  # provider failures are soft; surfaced through the normal toast path
                log.warning("background subtitle fetch failed", exc_info=True)
                reader._subtitle_results.put(
                    SubtitleFetchResult(
                        None,
                        f"Japanese subtitle fetch failed: {exc}",
                        select_if_unchanged,
                        initial_sid,
                        replace,
                        force_select,
                    )
                )
        finally:
            if on_done is not None:
                on_done()

    thread = threading.Thread(target=work, name=f"saitenka-{name}", daemon=True)
    reader._subtitle_fetch_threads.append(thread)
    thread.start()


def configure_retry(reader: Reader, factory: ProviderFetchFactory | None) -> None:
    reader.episode.subtitle.retry_factory = factory


def _finish_retry(reader: Reader) -> None:
    with reader.episode.subtitle.retry_lock:
        reader.episode.subtitle.retry_active = False


def _current_external_sub(ipc) -> Path | None:
    """The on-screen primary subtitle file, if it's an external srt (ours or a user ``--sub-file``)."""
    from pathlib import Path

    from overlay.app.embedded_subs import _selected_sub_track

    track = _selected_sub_track(ipc)
    ext = track.get("external-filename") if track else None
    return Path(ext) if ext else None


def _start_resync_window(reader: Reader, video_path: str, sub: Path) -> None:
    """Re-time the subs you already have from the CURRENT playhead onward (no provider query) — the
    user's "sync from here" shortcut. A drifting source (right after the OP, early before it) can't be
    fixed by one whole-file offset, so this re-times only the segment you're watching; press again at
    the next drift point. Falls back to a whole-file re-sync when the window can't align."""
    from pathlib import Path

    from overlay.app.resync import resync_current, resync_window

    playhead = reader._get("time-pos")
    start_s = float(playhead) if playhead is not None else 0.0
    reader._toast("Re-timing subtitles from here…")

    def do() -> tuple[Path | None, str]:
        out = resync_window(Path(video_path), sub, start_s=start_s)
        if out is None:  # window couldn't align → whole-file re-sync (in-place, returns sub)
            whole = resync_current(Path(video_path), sub)
            return whole, f"subtitles re-synced: {whole.name}"
        if out == sub:  # window already aligned here → nothing to swap
            return None, "subtitles already aligned here"
        return out, f"subtitles re-timed from {int(start_s)}s"

    start_fetch(
        reader, do, name="subtitle-resync", replace=True, on_done=lambda: _finish_retry(reader)
    )


def _start_provider_fetch(reader: Reader, video_path: str) -> None:
    factory = reader.episode.subtitle.retry_factory
    if factory is None:
        _finish_retry(reader)
        reader._toast("No Japanese subtitle providers enabled", "warn")
        return
    try:
        fetch = factory(video_path)
    except Exception as exc:
        _finish_retry(reader)
        log.warning("subtitle retry setup failed", exc_info=True)
        reader._toast(f"Japanese subtitle search failed: {exc}", "warn")
        return
    reader._toast("Searching Japanese subtitle providers…")
    start_fetch(
        reader, fetch, name="subtitle-retry", replace=True, on_done=lambda: _finish_retry(reader)
    )


def retry(reader: Reader) -> None:
    """The subtitle-sync keybind. If you already have subs on screen, RE-SYNC them in place (no
    provider query — you only want them re-timed). Only when there are no external subs does it fall
    back to querying providers."""
    video_path = reader._get("path")
    if not video_path:
        reader._toast("No media loaded for subtitle search", "warn")
        return
    with reader.episode.subtitle.retry_lock:
        if reader.episode.subtitle.retry_active:
            reader._toast("Subtitle sync already running", "warn")
            return
        reader.episode.subtitle.retry_active = True
    current = _current_external_sub(reader.ipc)
    if current is not None:
        _start_resync_window(reader, video_path, current)
    else:
        _start_provider_fetch(reader, video_path)


def _reset_sub_delay(reader: Reader) -> None:
    """Zero mpv's ``sub-delay`` when we (re-)establish authoritative timing by selecting our own track.
    Our subtitle file IS the timing source of truth — resync rewrites the cue timestamps in the file —
    so a residual delay must not ride on top. mpv restores ``sub-delay`` from watch-later across runs
    and keeps it across tracks, so a stale offset from a previous run/track would silently mistime a
    freshly file-timed track (found live: a resync looked wrong until sub-delay was hand-zeroed). The
    manual anchor key stays cumulative — it just refines from this clean 0 baseline after a load."""
    reader.ipc.command("set_property", "sub-delay", 0.0)


def _replace_japanese_track(
    reader: Reader, path, status: str, *, toast: str = "Japanese subtitles re-synced"
) -> None:
    """Swap the on-screen subtitle for a freshly fetched/re-synced file (the user's retry, or an
    explicit picker choice). Drops the stale external track(s) first — mpv caches an already-loaded
    external's cues in memory, and ``discover_tracks`` would pick the older duplicate JP — then re-adds
    + selects the fresh one and rebuilds the lookahead index, so the corrected timing shows immediately."""
    from overlay.app.embedded_subs import build_sub_index_for_current_track

    for track in sub_tracks(reader.ipc):
        if track.get("external") and track.get("id") is not None:
            reader.ipc.command("sub-remove", track["id"])
    reader.ipc.command("set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None
    reader.ipc.command("sub-add", str(path), "select", "", "jpn")  # "select" → mpv selects it now
    _reset_sub_delay(reader)  # our file is the timing truth; drop any persisted/stale mpv offset
    reader.jp_sid = reader._get("sid")  # the just-selected track, not discover_tracks' first JP
    reader.en_sid = discover_tracks(reader.ipc, reader.subtitle_slang).en_sid
    reader.subtitle_language = MAIN_LANG
    reader._sub_index = None
    reader.set_subtitle("")
    build_sub_index_for_current_track(reader)
    reader._toast(toast)
    log.info("%s", status)


def _add_background_japanese(reader: Reader, result: SubtitleFetchResult) -> None:
    """Non-disruptive arrival: add the fetched JP track but keep the current selection unless the user
    hasn't touched it and had no JP yet (then auto-select). Leaves English on screen for an explicit
    Alt+t otherwise — the background-fetch contract."""
    path, status = result.path, result.status
    current_sid = reader._get("sid")
    had_japanese = reader.jp_sid is not None
    reader.ipc.command("sub-add", str(path), "auto", "", "jpn")
    tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
    reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    select_japanese = (
        result.select_if_unchanged
        and not had_japanese
        and current_sid == result.initial_sid
        and reader.jp_sid is not None
    )
    if not select_japanese:
        reader.ipc.command("set_property", "sid", current_sid if current_sid is not None else "no")
        reader._toast("Japanese subtitles ready — Alt+t to switch")
        log.info("%s", status)
        return
    reader.ipc.command("set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None
    reader.ipc.command("set_property", "sid", reader.jp_sid)
    _reset_sub_delay(reader)  # our file is the timing truth; drop any persisted/stale mpv offset
    reader.subtitle_language = MAIN_LANG
    reader._sub_index = None
    reader.set_subtitle("")
    if reader._translation_visible():
        setup_secondary(reader)
    from overlay.app.embedded_subs import build_sub_index_for_current_track

    build_sub_index_for_current_track(reader)
    reader._toast("Japanese subtitles ready")
    log.info("%s", status)


def apply_fetch_results(reader: Reader) -> None:
    while True:
        try:
            result = reader._subtitle_results.get_nowait()
        except queue.Empty:
            return
        if result.path is None:
            log.warning("%s", result.status)
            reader._toast(result.status, "warn")
        # An explicit picker choice selects the chosen source NOW, from any current language (English
        # included) — the user picked it on purpose, so the keep-current background contract doesn't apply.
        elif result.force_select:
            _replace_japanese_track(
                reader, result.path, result.status, toast="Japanese subtitles selected"
            )
        # A user retry while watching Japanese swaps the on-screen (mistimed) track for the re-synced
        # file; from English it falls through to the non-disruptive add (fetch JP, keep EN until Alt+t).
        elif result.replace and reader.subtitle_language == MAIN_LANG:
            _replace_japanese_track(reader, result.path, result.status)
        else:
            _add_background_japanese(reader, result)
