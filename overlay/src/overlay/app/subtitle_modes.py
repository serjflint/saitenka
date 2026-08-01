"""Primary JP/EN subtitle selection and non-destructive background track arrival."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from overlay.app.controller import Reader

    ProviderFetch = Callable[[], tuple[Path | None, str]]
    ProviderFetchFactory = Callable[[str], ProviderFetch]

log = logging.getLogger(__name__)

Language = Literal["jp", "en"]
EN_LANGS = {"en", "eng", "en-us", "en-gb", "eng-us", "english"}


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
        active = "jp"
    elif tracks.en_sid is not None:
        active, sid = "en", tracks.en_sid
    if sid is not None:
        ipc.command("set_property", "sid", sid)
        ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC wire value
    return SubtitleStartup(tracks, active)


def configure(reader: Reader, startup: SubtitleStartup, *, slang: str = "ja,jpn,jp") -> None:
    reader.jp_sid = startup.tracks.jp_sid
    reader.en_sid = startup.tracks.en_sid
    reader.subtitle_language = startup.active or "jp"
    reader.subtitle_slang = slang
    from overlay.app import analysis_overlay

    analysis_overlay.on_index_changed(reader)


def setup_secondary(reader: Reader) -> int | None:
    if reader.jp_sid is None and reader.en_sid is None:
        tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
        reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    sid = reader.en_sid if reader.subtitle_language == "jp" else reader.jp_sid
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
    if sid == reader.jp_sid:
        language: Language = "jp"
    elif sid == reader.en_sid:
        language = "en"
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


def toggle(reader: Reader) -> None:
    tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
    reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    active_sid = reader._get("sid")
    if active_sid == reader.jp_sid:
        target: Language = "en"
    elif active_sid == reader.en_sid or (
        reader.subtitle_language == "jp" and reader.jp_sid is not None
    ):
        target = "jp"
    elif reader.subtitle_language == "en" and reader.en_sid is not None:
        target = "en"
    else:
        target = "jp" if reader.jp_sid is not None else "en"
    sid = reader.en_sid if target == "en" else reader.jp_sid
    if sid is None:
        reader._toast(f"{target.upper()} subtitles unavailable", "warn")
        return

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
    reader._toast(f"subtitle mode: {target.upper()}")


def start_fetch(
    reader: Reader,
    fetch: ProviderFetch,
    *,
    name: str = "sub-provider",
    on_done: Callable[[], None] | None = None,
) -> None:
    """Run provider I/O off-thread; mpv IPC stays on the reader thread."""

    def work() -> None:
        try:
            try:
                reader._subtitle_results.put(fetch())
            except (
                Exception
            ) as exc:  # provider failures are soft; surfaced through the normal toast path
                log.warning("background subtitle fetch failed", exc_info=True)
                reader._subtitle_results.put((None, f"Japanese subtitle fetch failed: {exc}"))
        finally:
            if on_done is not None:
                on_done()

    thread = threading.Thread(target=work, name=f"saitenka-{name}", daemon=True)
    reader._subtitle_fetch_threads.append(thread)
    thread.start()


def configure_retry(reader: Reader, factory: ProviderFetchFactory | None) -> None:
    reader._subtitle_retry_factory = factory


def _finish_retry(reader: Reader) -> None:
    with reader._subtitle_retry_lock:
        reader._subtitle_retry_active = False


def retry(reader: Reader) -> None:
    factory = reader._subtitle_retry_factory
    if factory is None:
        reader._toast("No Japanese subtitle providers enabled", "warn")
        return
    video_path = reader._get("path")
    if not video_path:
        reader._toast("No media loaded for subtitle search", "warn")
        return
    with reader._subtitle_retry_lock:
        if reader._subtitle_retry_active:
            reader._toast("Japanese subtitle search already running", "warn")
            return
        reader._subtitle_retry_active = True
    try:
        fetch = factory(str(video_path))
    except Exception as exc:
        _finish_retry(reader)
        log.warning("subtitle retry setup failed", exc_info=True)
        reader._toast(f"Japanese subtitle search failed: {exc}", "warn")
        return
    reader._toast("Searching Japanese subtitle providers…")
    start_fetch(reader, fetch, name="subtitle-retry", on_done=lambda: _finish_retry(reader))


def apply_fetch_results(reader: Reader) -> None:
    while True:
        try:
            path, status = reader._subtitle_results.get_nowait()
        except queue.Empty:
            return
        if path is None:
            log.warning("%s", status)
            reader._toast(status, "warn")
            continue
        retained_sid = reader.en_sid if reader.subtitle_language == "en" else reader.jp_sid
        reader.ipc.command("sub-add", str(path), "auto", "", "jpn")
        reader.ipc.command(
            "set_property", "sid", retained_sid if retained_sid is not None else "no"
        )
        tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
        reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
        reader._toast("Japanese subtitles ready — Alt+t to switch")
        log.info("%s", status)
