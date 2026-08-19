"""Primary JP/EN subtitle selection and non-destructive background track arrival."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app.languages import MAIN_LANG, Language
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.subtitle_selection import (
    FetchAction,
    SubtitleStartup,
    SubtitleTracks,
    fetch_action,
    language_name,
    selects_background_japanese,
    wanted_languages,
)
from saitenka.app.subtitle_selection import discover as _discover
from saitenka.app.subtitle_selection import initial as _initial
from saitenka.app.subtitle_selection import matching_track as _matching_track
from saitenka.app.subtitle_selection import primary_role as _primary_role_for
from saitenka.runtime import EffectFinished, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path

    from saitenka.app.controller import Reader
    from saitenka.app.reader_context import SubtitleSource

    ProviderFetch = Callable[[], tuple[Path | None, str]]
    ProviderFetchFactory = Callable[[str], ProviderFetch]

log = logging.getLogger(__name__)


def sub_tracks(ipc) -> list[dict]:
    data = ipc.command("get_property", "track-list").get("data") or []
    return [track for track in data if track.get("type") == "sub"]


def _send(ipc, identity: str, *command: object) -> None:
    """Send one correlated subtitle command through the gateway."""
    send_correlated(ipc, identity, *command, owner=Owner.SUBTITLE)


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


@dataclass(frozen=True, slots=True)
class SubtitleFetchRequest:
    fetch: ProviderFetch
    select_if_unchanged: bool
    initial_sid: int | str | None
    replace: bool
    force_select: bool


class JobSubmitter(Protocol):
    def __call__(
        self,
        *,
        owner: Owner,
        identity: object,
        lane: str,
        request: object,
        on_finished: Callable[[EffectFinished], None],
    ) -> bool: ...


def run_fetch(request: object, cancelled: threading.Event) -> object:
    if not isinstance(request, SubtitleFetchRequest):
        raise TypeError("invalid subtitle fetch request")
    if cancelled.is_set():
        return None
    try:
        path, status = request.fetch()
    except Exception as exc:  # provider failures are soft and user-visible
        log.warning("background subtitle fetch failed", exc_info=True)
        path, status = None, f"Japanese subtitle fetch failed: {exc}"
    return SubtitleFetchResult(
        path,
        status,
        request.select_if_unchanged,
        request.initial_sid,
        request.replace,
        request.force_select,
    )


def configure_runtime_job(ipc) -> JobSubmitter | None:
    register = getattr(ipc, "register_runtime_job_lane", None)
    if register is None or not register(
        "subtitle-fetch",
        JobLanePolicy(capacity=4, workers=2),
        run_fetch,
    ):
        return None
    return ipc.submit_runtime_job


def finish_fetch(request: SubtitleFetchRequest, completion: EffectFinished) -> SubtitleFetchResult:
    result = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
    if isinstance(result, SubtitleFetchResult):
        return result
    return unavailable_fetch(request)


def unavailable_fetch(request: SubtitleFetchRequest) -> SubtitleFetchResult:
    return SubtitleFetchResult(
        None,
        "Japanese subtitle fetch unavailable",
        request.select_if_unchanged,
        request.initial_sid,
        request.replace,
        request.force_select,
    )


def has_track_for_slang(ipc, slang: str) -> bool:
    """Whether a subtitle track is TAGGED with one of ``slang``'s languages — no untagged fallback
    (unlike :func:`discover_tracks`, which grabs the first track when nothing matches). The live profile
    switcher gates on this: cycling to a language the file has no track for keeps the current track and
    warns, instead of silently grabbing an unrelated one."""
    return _matching_track(sub_tracks(ipc), wanted_languages(slang)) is not None


def discover_tracks(ipc, slang: str = "ja,jpn,jp") -> SubtitleTracks:
    return _discover(sub_tracks(ipc), slang)


def select_initial(ipc, slang: str = "ja,jpn,jp") -> SubtitleStartup:
    """Prefer Japanese, fall back to tagged English, and leave a missing-both file untouched."""
    startup = _initial(sub_tracks(ipc), slang)
    sid = startup.tracks.jp_sid if startup.active == MAIN_LANG else startup.tracks.en_sid
    if sid is not None:
        _send(ipc, "select-primary", "set_property", "sid", sid)
    return startup


def configure(reader: Reader, startup: SubtitleStartup, *, slang: str = "ja,jpn,jp") -> None:
    reader.jp_sid = startup.tracks.jp_sid
    reader.en_sid = startup.tracks.en_sid
    reader.subtitle_language = startup.active or MAIN_LANG
    reader.subtitle_slang = slang
    reader.subtitle_pipeline.activate(reader)
    if reader._get("secondary-sid") not in {None, False, "no"}:
        _send(reader.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    # Null the mirror too: configure now runs mid-session (a live profile cycle re-selects the track),
    # where a stale _translation_secondary_sid would leave the EN reveal stuck off — setup_secondary's
    # `mirror == sid` guard would skip re-issuing secondary-sid. At launch the mirror is already None.
    reader._translation_secondary_sid = None

    reader.invalidate_analysis()


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
    _send(reader.ipc, "select-secondary", "set_property", "secondary-sid", sid)
    _send(reader.ipc, "hide-secondary", "set_property", "secondary-sub-visibility", False)  # noqa: FBT003  # mpv IPC wire value
    reader._translation_secondary_sid = sid
    return sid


def release_secondary(reader: Reader) -> None:
    if reader._translation_secondary_sid is None:
        return
    _send(reader.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None


def on_primary_changed(reader: Reader, sid) -> None:
    if sid == reader._translation_secondary_sid:
        return
    announce_track(reader, sid)
    if sid is None:
        return
    known = sid in {reader.jp_sid, reader.en_sid}
    if not known:
        # The user just made this track primary (manual cycle / drag-'n'-drop). Index it from disk
        # FIRST, so an untagged track can be classified by its actual content just below.
        reader._sub_index = None
        from saitenka.app.embedded_subs import build_sub_index_for_current_track

        build_sub_index_for_current_track(reader)
    language = _primary_role(reader, sid)
    if not known:
        if language == MAIN_LANG:
            reader.jp_sid = sid
        else:
            reader.en_sid = sid
        log.info("subtitle sid=%s adopted as %s", sid, language)
    if language != reader.subtitle_language:
        reader.subtitle_language = language

        reader.invalidate_analysis()
    if reader._translation_visible():
        setup_secondary(reader)
    else:
        release_secondary(reader)


def _primary_role(reader: Reader, sid) -> Language:
    tracks = SubtitleTracks(reader.jp_sid, reader.en_sid)
    lang = next((t.get("lang") for t in sub_tracks(reader.ipc) if t.get("id") == sid), None)
    return _primary_role_for(
        sid, tracks, track_lang=lang, sample=_sample_cue_text(reader._sub_index, reader.sub_text)
    )


def _sample_cue_text(sub_index, sub_text: str, limit: int = 20) -> str:
    """A few cues of the current track for content-based language ID: the freshly indexed cues if
    present, else mpv's on-screen cue."""
    if sub_index is not None and len(sub_index) > 0:
        return " ".join(cue.text for cue in sub_index.cues[:limit])
    return sub_text or ""


def announce_track(reader: Reader, sid) -> None:
    if sid == reader._last_announced_sid:
        return
    tracks = sub_tracks(reader.ipc)
    for index, track in enumerate(tracks, 1):
        if track.get("id") == sid:
            reader._last_announced_sid = sid
            name = language_name(track.get("lang"))
            # Log the same signal the toast shows: a surprising "unknown language (10/11)" here is the
            # earliest sign of a wrong-track selection, and belongs in the bundle, not just on screen.
            log.info("subtitles announced: %s (%d/%d) sid=%s", name, index, len(tracks), sid)
            reader._toast(f"subtitles: {name} ({index}/{len(tracks)})")
            return


def select_track(reader: Reader, sid: int, target: Language) -> None:
    """Carry out a decided language switch: make ``sid`` primary and adopt ``target`` as the role."""
    tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
    reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    _send(reader.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None
    _send(reader.ipc, "select-primary", "set_property", "sid", sid)
    reader.subtitle_language = target
    reader._sub_index = None

    reader.invalidate_analysis()
    reader.set_subtitle("")
    if reader._translation_visible():
        setup_secondary(reader)
    else:
        release_secondary(reader)
    from saitenka.app.embedded_subs import build_sub_index_for_current_track

    build_sub_index_for_current_track(reader)
    announce_track(reader, sid)


def adopt_current_as_target(reader: Reader, sid) -> None:
    """Override: treat mpv's current primary subtitle track as the Japanese target, whatever its tag.
    The manual escape hatch — bound to a key so the user acts in mpv directly — for the rare case
    auto-adoption guessed wrong (an untagged track that is really English) or never fired."""
    reader.jp_sid = sid
    if reader.en_sid == sid:
        reader.en_sid = None
    if reader.subtitle_language != MAIN_LANG:
        reader.subtitle_language = MAIN_LANG

        reader.invalidate_analysis()
    reader._sub_index = None
    from saitenka.app.embedded_subs import build_sub_index_for_current_track

    build_sub_index_for_current_track(reader)
    reader.set_subtitle(reader.sub_text)  # recolor the on-screen cue now, don't wait for the next
    log.info("user forced subtitle sid=%s as the Japanese primary", sid)


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

    request = SubtitleFetchRequest(fetch, select_if_unchanged, initial_sid, replace, force_select)
    reader._submit_subtitle_fetch(request, name=name, on_done=on_done)


def configure_retry(source: SubtitleSource, factory: ProviderFetchFactory | None) -> None:
    source.retry_factory = factory


def _finish_retry(source: SubtitleSource) -> None:
    with source.retry_lock:
        source.retry_active = False


def _current_external_sub(ipc) -> Path | None:
    """The on-screen primary subtitle file, if it's an external srt (ours or a user ``--sub-file``)."""
    from pathlib import Path

    from saitenka.app.embedded_subs import _selected_sub_track

    track = _selected_sub_track(ipc)
    ext = track.get("external-filename") if track else None
    return Path(ext) if ext else None


def _start_resync_window(reader: Reader, video_path: str, sub: Path) -> None:
    """Re-time the subs you already have from the CURRENT playhead onward (no provider query) — the
    user's "sync from here" shortcut. A drifting source (right after the OP, early before it) can't be
    fixed by one whole-file offset, so this derives the offset from a local slice around the playhead and
    re-times from there; press again at the next drift point. Falls back to a whole-file re-sync when the
    window can't align."""
    from pathlib import Path

    from saitenka.app.resync import resync_current, resync_window

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
        reader,
        do,
        name="subtitle-resync",
        replace=True,
        on_done=lambda: _finish_retry(reader.episode.subtitle),
    )


def _start_provider_fetch(reader: Reader, video_path: str) -> None:
    factory = reader.episode.subtitle.retry_factory
    if factory is None:
        _finish_retry(reader.episode.subtitle)
        reader._toast("No Japanese subtitle providers enabled", "warn")
        return
    try:
        fetch = factory(video_path)
    except Exception as exc:
        _finish_retry(reader.episode.subtitle)
        log.warning("subtitle retry setup failed", exc_info=True)
        reader._toast(f"Japanese subtitle search failed: {exc}", "warn")
        return
    reader._toast("Searching Japanese subtitle providers…")
    start_fetch(
        reader,
        fetch,
        name="subtitle-retry",
        replace=True,
        on_done=lambda: _finish_retry(reader.episode.subtitle),
    )


def _claim_retry(state) -> bool:
    """Take the single-flight retry slot. The reducer already rejected the common re-entry; this
    closes the window between reading that fact and acting on it."""
    with state.retry_lock:
        if state.retry_active:
            return False
        state.retry_active = True
    return True


def begin_acquisition(reader: Reader, video_path: str, source) -> None:
    """Carry out a decided subtitle acquisition. Re-timing needs the external file that was on
    screen when the decision was made; if it went away, fall back to querying providers."""
    from saitenka.app.subtitle_intents import AcquisitionSource

    current = (
        _current_external_sub(reader.ipc) if source is AcquisitionSource.RESYNC_CURRENT else None
    )
    if not _claim_retry(reader.episode.subtitle):
        reader._toast("Subtitle sync already running", "warn")
        return
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
    _send(reader.ipc, "reset-sub-delay", "set_property", "sub-delay", 0.0)


def _replace_japanese_track(
    reader: Reader, path, status: str, *, toast: str = "Japanese subtitles re-synced"
) -> None:
    """Swap the on-screen subtitle for a freshly fetched/re-synced file (the user's retry, or an
    explicit picker choice). Drops the stale external track(s) first — mpv caches an already-loaded
    external's cues in memory, and ``discover_tracks`` would pick the older duplicate JP — then re-adds
    + selects the fresh one and rebuilds the lookahead index, so the corrected timing shows immediately."""
    from saitenka.app.embedded_subs import build_sub_index_for_current_track

    for track in sub_tracks(reader.ipc):
        if track.get("external") and track.get("id") is not None:
            _send(reader.ipc, "remove-external", "sub-remove", track["id"])
    _send(reader.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None
    _send(
        reader.ipc, "add-japanese", "sub-add", str(path), "select", "", "jpn"
    )  # mpv selects it now
    _reset_sub_delay(reader)  # our file is the timing truth; drop any persisted/stale mpv offset
    reader.jp_sid = reader._get("sid")  # the just-selected track, not discover_tracks' first JP
    reader.en_sid = discover_tracks(reader.ipc, reader.subtitle_slang).en_sid
    reader.subtitle_language = MAIN_LANG
    reader.set_subtitle("")
    build_sub_index_for_current_track(reader)  # replaces the index on success; retains it if the
    # just-added track can't resolve yet (rebuild is fail-soft) rather than blanking the cues
    reader._toast(toast)
    log.info("%s", status)


def _add_background_japanese(reader: Reader, result: SubtitleFetchResult) -> None:
    """Non-disruptive arrival: add the fetched JP track but keep the current selection unless the user
    hasn't touched it and had no JP yet (then auto-select). Leaves English on screen for an explicit
    Alt+t otherwise — the background-fetch contract."""
    path, status = result.path, result.status
    current_sid = reader._get("sid")
    had_japanese = reader.jp_sid is not None
    _send(reader.ipc, "add-japanese-background", "sub-add", str(path), "auto", "", "jpn")
    tracks = discover_tracks(reader.ipc, reader.subtitle_slang)
    reader.jp_sid, reader.en_sid = tracks.jp_sid, tracks.en_sid
    select_japanese = selects_background_japanese(
        select_if_unchanged=result.select_if_unchanged,
        had_japanese=had_japanese,
        current_sid=current_sid,
        initial_sid=result.initial_sid,
        jp_sid=reader.jp_sid,
    )
    if not select_japanese:
        _send(
            reader.ipc,
            "keep-primary",
            "set_property",
            "sid",
            current_sid if current_sid is not None else "no",
        )
        reader._toast("Japanese subtitles ready — Alt+t to switch")
        log.info("%s", status)
        return
    _send(reader.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    reader._translation_secondary_sid = None
    _send(reader.ipc, "select-japanese", "set_property", "sid", reader.jp_sid)
    _reset_sub_delay(reader)  # our file is the timing truth; drop any persisted/stale mpv offset
    reader.subtitle_language = MAIN_LANG
    reader.set_subtitle("")
    if reader._translation_visible():
        setup_secondary(reader)
    from saitenka.app.embedded_subs import build_sub_index_for_current_track

    build_sub_index_for_current_track(
        reader
    )  # replaces on success; retains prior cues if unresolved
    reader._toast("Japanese subtitles ready")
    log.info("%s", status)


def apply_fetch_result(reader: Reader, result: SubtitleFetchResult) -> None:
    action = fetch_action(
        path_available=result.path is not None,
        force_select=result.force_select,
        replace=result.replace,
        language=reader.subtitle_language,
    )
    if action is FetchAction.REPORT_FAILURE:
        log.warning("%s", result.status)
        reader._toast(result.status, "warn")
    elif action is FetchAction.REPLACE:
        toast = "Japanese subtitles selected" if result.force_select else None
        _replace_japanese_track(
            reader,
            result.path,
            result.status,
            **({"toast": toast} if toast else {}),
        )
    else:
        _add_background_japanese(reader, result)
