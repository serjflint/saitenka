"""Primary JP/EN subtitle selection and non-destructive background track arrival."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka_tokenize.languages import MAIN_LANG, Language

from saitenka.app.mpv_egress import send_correlated
from saitenka.app.subtitle_ownership import ASK_MPV, SelectedSid
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
from saitenka.runtime.events import (
    SubtitleLanguageChanged,
    SubtitlePrimaryAdopted,
    SubtitleSecondaryLeased,
    SubtitleStartupConfigured,
    SubtitleTrackAnnounced,
    SubtitleTracksDiscovered,
    SubtitleTranslationConfigured,
)
from saitenka.runtime.jobs import JobLanePolicy, JobSubmitter, configure_lane

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path

    from saitenka.runtime.events import SubtitleEvent
    from saitenka.runtime.subtitle import SubtitleTrackState

    ProviderFetch = Callable[[], tuple[Path | None, str]]
    ProviderFetchFactory = Callable[[str], ProviderFetch]

log = logging.getLogger(__name__)


def sub_tracks(ipc) -> list[dict]:
    data = ipc.query("track-list") or []
    return [track for track in data if track.get("type") == "sub"]


def _send(ipc, identity: str, *command: object) -> None:
    """Send one correlated subtitle command through the gateway."""
    send_correlated(ipc, identity, *command, owner=Owner.SUBTITLE)


@dataclass(frozen=True)
class SubtitleFetchResult:
    path: Path | None
    status: str
    select_if_unchanged: bool
    #: The selection at submit time. `object` because the only question asked of it is whether
    #: it still equals the current one — mpv answers `sid` with an int or a string.
    initial_sid: object
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
    initial_sid: object
    replace: bool
    force_select: bool


if TYPE_CHECKING:
    PropertyGet = Callable[[str], object]
    Toast = Callable[..., None]


class SubtitleRetryState(Protocol):
    retry_factory: ProviderFetchFactory | None
    retry_active: bool
    retry_lock: threading.Lock


class FetchSubmitter(Protocol):
    def __call__(
        self,
        request: SubtitleFetchRequest,
        *,
        name: str,
        on_done: Callable[[], None] | None = None,
    ) -> None: ...


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
    return configure_lane(
        ipc,
        "subtitle-fetch",
        JobLanePolicy(capacity=4, workers=2),
        run_fetch,
    )


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


def discover_tracks(ipc, slang: str = "ja,jpn,jp", second_slang: str = "en") -> SubtitleTracks:
    return _discover(sub_tracks(ipc), slang, second_slang)


def selected_sid(startup: SubtitleStartup) -> int | str | None:
    """The track `select_initial` selects for this startup — the one fact both it and `configure` need.

    Derived in one place because the two disagreeing is invisible: `configure` would declare a track
    nobody selected, and the ownership epoch would name it for the rest of the session.
    """
    return startup.tracks.jp_sid if startup.active == MAIN_LANG else startup.tracks.en_sid


def select_initial(ipc, slang: str = "ja,jpn,jp", second_slang: str = "en") -> SubtitleStartup:
    """Prefer Japanese, fall back to tagged English, and leave a missing-both file untouched."""
    startup = _initial(sub_tracks(ipc), slang, second_slang)
    sid = selected_sid(startup)
    if sid is not None:
        _send(ipc, "select-primary", "set_property", "sid", sid)
    return startup


def configure(
    startup: SubtitleStartup,
    *,
    slang: str = "ja,jpn,jp",
    second_slang: str = "en",
    declare: Callable[[SubtitleEvent], object],
    activate: Callable[[SelectedSid], None],
    secondary_sid: object,
    ipc,
    invalidate: Callable[[], None],
) -> None:
    """Adopt a startup selection: declare it, take the pixels for it, drop a stale secondary.

    Takes the facts rather than the host, and `activate` is handed in for the reason the
    coordinator's own `activate` takes a `draw`: building the renderer's target reaches fifteen
    presentation members this decision has no use for, so the caller that already holds them binds
    it.
    """
    declare(
        SubtitleStartupConfigured(
            startup.tracks.jp_sid,
            startup.tracks.en_sid,
            startup.active or MAIN_LANG,
            slang,
            second_slang,
        )
    )
    # Declare the track rather than let the renderer read it back. `select_initial` wrote `sid`
    # fire-and-forget moments ago, so mid-session (a live profile cycle) mpv has not echoed it yet
    # and `observed_property("sid")` still answers with the track being replaced — the selection would look
    # unchanged and the pixels would stay owned on behalf of a track that is gone. When nothing was
    # written there is nothing to declare and the read is correct.
    sid = selected_sid(startup)
    activate(ASK_MPV if sid is None else sid)
    if secondary_sid not in {None, False, "no"}:
        _send(ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    invalidate()


@dataclass(frozen=True, slots=True)
class TrackPorts:
    """What deciding a track needs from the session, named rather than reached for.

    `tracks` is a callable and not the slice itself: every declaration produces a new state and
    this family reads back what it just declared, so a bound value would be one turn stale. The
    index pair is two ports rather than one because `select_track` does other work between
    dropping the old track's cues and building the new track's.
    """

    ipc: object
    get: PropertyGet
    toast: Toast
    tracks: Callable[[], SubtitleTrackState]
    declare: Callable[[SubtitleEvent], SubtitleTrackState]
    invalidate: Callable[[], None]
    translation_visible: Callable[[], bool]
    drop_index: Callable[[], None]
    rebuild_index: Callable[[], None]
    sample_cue: Callable[[], str]
    clear_cue: Callable[[], None]
    redraw_cue: Callable[[], None]


def setup_secondary(ports: TrackPorts) -> int | None:
    state = ports.tracks()
    if state.jp_sid is None and state.en_sid is None:
        found = discover_tracks(ports.ipc, state.slang, state.second_slang)
        state = ports.declare(SubtitleTracksDiscovered(found.jp_sid, found.en_sid))
    sid = state.translation_sid
    if sid is None or sid == ports.get("sid"):
        release_secondary(ports)
        return None
    if state.secondary_sid == sid:
        return sid
    _send(ports.ipc, "select-secondary", "set_property", "secondary-sid", sid)
    _send(ports.ipc, "hide-secondary", "set_property", "secondary-sub-visibility", False)  # noqa: FBT003  # mpv IPC wire value
    ports.declare(SubtitleSecondaryLeased(sid))
    return sid


def release_secondary(ports: TrackPorts) -> None:
    if ports.tracks().secondary_sid is None:
        return
    clear_secondary(ports.ipc)
    ports.declare(SubtitleSecondaryLeased(None))


def clear_secondary(ipc) -> None:
    """Drop mpv's secondary subtitle track — the EN reveal's lease on it."""
    _send(ipc, "clear-secondary", "set_property", "secondary-sid", "no")


def select_translation(ports: TrackPorts, second_slang: str) -> None:
    """Re-resolve only the translation role when a new primary is unavailable."""
    state = ports.tracks()
    found = discover_tracks(ports.ipc, state.slang, second_slang)
    ports.declare(SubtitleTranslationConfigured(found.en_sid, second_slang))
    if ports.translation_visible():
        setup_secondary(ports)
    else:
        release_secondary(ports)


def on_primary_changed(ports: TrackPorts, sid) -> None:
    state = ports.tracks()
    if sid == state.secondary_sid:
        return
    announce_track(ports, sid)
    if sid is None:
        return
    known = sid in {state.jp_sid, state.en_sid}
    if not known:
        # The user just made this track primary (manual cycle / drag-'n'-drop). Index it from disk
        # FIRST, so an untagged track can be classified by its actual content just below.
        ports.drop_index()
        ports.rebuild_index()
    language = _primary_role(
        ports.ipc,
        sid,
        SubtitleTracks(state.jp_sid, state.en_sid),
        ports.sample_cue(),
        state.slang,
        state.second_slang,
    )
    changed = language != state.language
    if not known:
        # One declaration for both halves: the adoption *is* what makes this track's role true,
        # and the reducer's steal rule is why they cannot be two events that disagree.
        ports.declare(SubtitlePrimaryAdopted(sid, language))
        log.info("subtitle sid=%s adopted as %s", sid, language)
    elif changed:
        ports.declare(SubtitleLanguageChanged(language))
    if changed:
        ports.invalidate()
    if ports.translation_visible():
        setup_secondary(ports)
    else:
        release_secondary(ports)


def _primary_role(
    ipc, sid, tracks: SubtitleTracks, sample: str, primary_slang: str, second_slang: str
) -> Language:
    """Which role a newly-primary track plays, from its tag or its content.

    Takes the facts, like `_sample_cue_text` beside it. `ipc` stays because the track list is a
    query, not a fact the caller holds — it is the mpv-read contract, not the host.
    """
    lang = next((t.get("lang") for t in sub_tracks(ipc) if t.get("id") == sid), None)
    return _primary_role_for(
        sid,
        tracks,
        track_lang=lang,
        sample=sample,
        primary_slang=primary_slang,
        second_slang=second_slang,
    )


def _sample_cue_text(sub_index, sub_text: str, limit: int = 20) -> str:
    """A few cues of the current track for content-based language ID: the freshly indexed cues if
    present, else mpv's on-screen cue."""
    if sub_index is not None and len(sub_index) > 0:
        return " ".join(cue.text for cue in sub_index.cues[:limit])
    return sub_text or ""


def announce_track(ports: TrackPorts, sid) -> None:
    if sid == ports.tracks().announced_sid:
        return
    tracks = sub_tracks(ports.ipc)
    for index, track in enumerate(tracks, 1):
        if track.get("id") == sid:
            ports.declare(SubtitleTrackAnnounced(sid))
            name = language_name(track.get("lang"))
            # Log the same signal the toast shows: a surprising "unknown language (10/11)" here is the
            # earliest sign of a wrong-track selection, and belongs in the bundle, not just on screen.
            log.info("subtitles announced: %s (%d/%d) sid=%s", name, index, len(tracks), sid)
            ports.toast(f"subtitles: {name} ({index}/{len(tracks)})")
            return


def select_track(ports: TrackPorts, sid: int, target: Language) -> None:
    """Carry out a decided language switch: make ``sid`` primary and adopt ``target`` as the role."""
    state = ports.tracks()
    tracks = discover_tracks(ports.ipc, state.slang, state.second_slang)
    ports.declare(SubtitleTracksDiscovered(tracks.jp_sid, tracks.en_sid))
    _send(ports.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    ports.declare(SubtitleSecondaryLeased(None))
    _send(ports.ipc, "select-primary", "set_property", "sid", sid)
    ports.declare(SubtitleLanguageChanged(target))
    ports.drop_index()

    ports.invalidate()
    ports.clear_cue()
    if ports.translation_visible():
        setup_secondary(ports)
    else:
        release_secondary(ports)
    ports.rebuild_index()
    announce_track(ports, sid)


def adopt_current_as_target(ports: TrackPorts, sid) -> None:
    """Override: treat mpv's current primary subtitle track as the Japanese target, whatever its tag.
    The manual escape hatch — bound to a key so the user acts in mpv directly — for the rare case
    auto-adoption guessed wrong (an untagged track that is really English) or never fired."""
    changed = ports.tracks().language != MAIN_LANG
    # The reducer takes the sid off the English role if it held it — the whole point of the
    # override is that the track on screen was filed wrong.
    ports.declare(SubtitlePrimaryAdopted(sid, MAIN_LANG))
    if changed:
        ports.invalidate()
    ports.drop_index()
    ports.rebuild_index()
    ports.redraw_cue()  # recolor the on-screen cue now, don't wait for the next
    log.info("user forced subtitle sid=%s as the Japanese primary", sid)


def start_fetch(
    submit: FetchSubmitter,
    get: PropertyGet,
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
    initial_sid = get("sid") if select_if_unchanged else None

    request = SubtitleFetchRequest(fetch, select_if_unchanged, initial_sid, replace, force_select)
    submit(request, name=name, on_done=on_done)


def configure_retry(source: SubtitleRetryState, factory: ProviderFetchFactory | None) -> None:
    source.retry_factory = factory


def _finish_retry(source: SubtitleRetryState) -> None:
    with source.retry_lock:
        source.retry_active = False


def _current_external_sub(ipc) -> Path | None:
    """The on-screen primary subtitle file, if it's an external srt (ours or a user ``--sub-file``)."""
    from pathlib import Path

    from saitenka.app.embedded_subs import _selected_sub_track

    track = _selected_sub_track(ipc)
    ext = track.get("external-filename") if track else None
    return Path(ext) if ext else None


def _published(sub: Path, retimed: Path) -> Path:
    """Publish a re-time of a hand-picked entry into the resyncing cache slot, and load the
    published copy — so pressing "sync from here" once survives the next launch instead of leaving
    the drift to be re-corrected every session. Falls back to the re-timed file where there is no
    slot to publish into (a `--sub-file`, a sibling next to the video)."""
    from saitenka.app.subtitle_cache import publish_retimed

    return publish_retimed(sub, retimed) or retimed


def _start_resync_window(
    submit: FetchSubmitter,
    get: PropertyGet,
    toast: Toast,
    retry: SubtitleRetryState,
    video_path: str,
    sub: Path,
) -> None:
    """Re-time the subs you already have from the CURRENT playhead onward (no provider query) — the
    user's "sync from here" shortcut. A drifting source (right after the OP, early before it) can't be
    fixed by one whole-file offset, so this derives the offset from a local slice around the playhead and
    re-times from there; press again at the next drift point. A failed alignment keeps the current track
    unchanged and reports the failure."""
    from pathlib import Path

    from saitenka.app.resync import resync_window

    playhead = get("time-pos")
    start_s = float(playhead) if isinstance(playhead, int | float) else 0.0
    toast("Re-timing subtitles from here…")

    def do() -> tuple[Path | None, str]:
        out = resync_window(Path(video_path), sub, start_s=start_s)
        if out is None:
            return None, "Subtitle retiming failed — current subtitles kept"
        if out == sub:  # window already aligned here → nothing to swap
            return None, "subtitles already aligned here"
        return _published(sub, out), f"subtitles re-timed from {int(start_s)}s"

    start_fetch(
        submit,
        get,
        do,
        name="subtitle-resync",
        replace=True,
        on_done=lambda: _finish_retry(retry),
    )


def _start_provider_fetch(
    submit: FetchSubmitter,
    get: PropertyGet,
    toast: Toast,
    retry: SubtitleRetryState,
    video_path: str,
) -> None:
    factory = retry.retry_factory
    if factory is None:
        _finish_retry(retry)
        toast("No Japanese subtitle providers enabled", "warn")
        return
    try:
        fetch = factory(video_path)
    except Exception as exc:
        _finish_retry(retry)
        log.warning("subtitle retry setup failed", exc_info=True)
        toast(f"Japanese subtitle search failed: {exc}", "warn")
        return
    toast("Searching Japanese subtitle providers…")
    start_fetch(
        submit,
        get,
        fetch,
        name="subtitle-retry",
        replace=True,
        on_done=lambda: _finish_retry(retry),
    )


def _claim_retry(state) -> bool:
    """Take the single-flight retry slot. The reducer already rejected the common re-entry; this
    closes the window between reading that fact and acting on it."""
    with state.retry_lock:
        if state.retry_active:
            return False
        state.retry_active = True
    return True


def begin_acquisition(
    submit: FetchSubmitter,
    get: PropertyGet,
    toast: Toast,
    retry: SubtitleRetryState,
    ipc,
    video_path: str,
    source,
) -> None:
    """Carry out a decided subtitle acquisition. Re-timing needs the external file that was on
    screen when the decision was made; if it went away, fall back to querying providers."""
    from saitenka.app.subtitle_intents import AcquisitionSource

    current = _current_external_sub(ipc) if source is AcquisitionSource.RESYNC_CURRENT else None
    if not _claim_retry(retry):
        toast("Subtitle sync already running", "warn")
        return
    if current is not None:
        _start_resync_window(submit, get, toast, retry, video_path, current)
    else:
        _start_provider_fetch(submit, get, toast, retry, video_path)


def _reset_sub_delay(ipc) -> None:
    """Zero mpv's ``sub-delay`` when we (re-)establish authoritative timing by selecting our own track.
    Our subtitle file IS the timing source of truth — resync rewrites the cue timestamps in the file —
    so a residual delay must not ride on top. mpv restores ``sub-delay`` from watch-later across runs
    and keeps it across tracks, so a stale offset from a previous run/track would silently mistime a
    freshly file-timed track (found live: a resync looked wrong until sub-delay was hand-zeroed). The
    manual anchor key stays cumulative — it just refines from this clean 0 baseline after a load."""
    _send(ipc, "reset-sub-delay", "set_property", "sub-delay", 0.0)


def _replace_japanese_track(
    ports: TrackPorts, path, status: str, *, toast: str = "Japanese subtitles re-synced"
) -> None:
    """Swap the on-screen subtitle for a freshly fetched/re-synced file (the user's retry, or an
    explicit picker choice). Drops the stale external track(s) first — mpv caches an already-loaded
    external's cues in memory, and ``discover_tracks`` would pick the older duplicate JP — then re-adds
    + selects the fresh one and rebuilds the lookahead index, so the corrected timing shows immediately."""
    for track in sub_tracks(ports.ipc):
        if track.get("external") and track.get("id") is not None:
            _send(ports.ipc, "remove-external", "sub-remove", track["id"])
    _send(ports.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    ports.declare(SubtitleSecondaryLeased(None))
    _send(
        ports.ipc, "add-japanese", "sub-add", str(path), "select", "", "jpn"
    )  # mpv selects it now
    _reset_sub_delay(ports.ipc)  # our file is the timing truth; drop any persisted/stale offset
    # The just-selected track, not discover_tracks' first JP. mpv answers `sid` with a track id or
    # None; the string form ("no") only ever goes the other way, on a write.
    selected = ports.get("sid")
    ports.declare(
        SubtitleTracksDiscovered(
            selected if isinstance(selected, int) else None,
            discover_tracks(ports.ipc, ports.tracks().slang, ports.tracks().second_slang).en_sid,
        )
    )
    ports.declare(SubtitleLanguageChanged(MAIN_LANG))
    ports.clear_cue()
    ports.rebuild_index()  # replaces the index on success; retains it if the just-added track
    # can't resolve yet (rebuild is fail-soft) rather than blanking the cues
    ports.toast(toast)
    log.info("%s", status)


def _add_background_japanese(ports: TrackPorts, result: SubtitleFetchResult) -> None:
    """Non-disruptive arrival: add the fetched JP track but keep the current selection unless the user
    hasn't touched it and had no JP yet (then auto-select). Leaves English on screen for an explicit
    Alt+t otherwise — the background-fetch contract."""
    path, status = result.path, result.status
    current_sid = ports.get("sid")
    state = ports.tracks()
    had_japanese = state.jp_sid is not None
    _send(ports.ipc, "add-japanese-background", "sub-add", str(path), "auto", "", "jpn")
    found = discover_tracks(ports.ipc, state.slang, state.second_slang)
    state = ports.declare(SubtitleTracksDiscovered(found.jp_sid, found.en_sid))
    select_japanese = selects_background_japanese(
        select_if_unchanged=result.select_if_unchanged,
        had_japanese=had_japanese,
        current_sid=current_sid,
        initial_sid=result.initial_sid,
        jp_sid=state.jp_sid,
    )
    if not select_japanese:
        _send(
            ports.ipc,
            "keep-primary",
            "set_property",
            "sid",
            current_sid if current_sid is not None else "no",
        )
        ports.toast("Japanese subtitles ready — Alt+t to switch")
        log.info("%s", status)
        return
    _send(ports.ipc, "clear-secondary", "set_property", "secondary-sid", "no")
    ports.declare(SubtitleSecondaryLeased(None))
    _send(ports.ipc, "select-japanese", "set_property", "sid", state.jp_sid)
    _reset_sub_delay(ports.ipc)  # our file is the timing truth; drop any persisted/stale offset
    ports.declare(SubtitleLanguageChanged(MAIN_LANG))
    ports.clear_cue()
    if ports.translation_visible():
        setup_secondary(ports)
    ports.rebuild_index()  # replaces on success; retains prior cues if unresolved
    ports.toast("Japanese subtitles ready")
    log.info("%s", status)


def apply_fetch_result(ports: TrackPorts, result: SubtitleFetchResult) -> None:
    action = fetch_action(
        path_available=result.path is not None,
        force_select=result.force_select,
        replace=result.replace,
        language=ports.tracks().language,
    )
    if action is FetchAction.REPORT_FAILURE:
        log.warning("%s", result.status)
        ports.toast(result.status, "warn")
    elif action is FetchAction.REPLACE:
        toast = "Japanese subtitles selected" if result.force_select else None
        _replace_japanese_track(
            ports,
            result.path,
            result.status,
            **({"toast": toast} if toast else {}),
        )
    else:
        _add_background_japanese(ports, result)
