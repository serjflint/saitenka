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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from overlay.app.languages import MAIN_LANG, SECOND_LANG
from overlay.app.subtitle_modes import (
    lang_matches as _lang_matches,
)
from overlay.app.subtitle_modes import (
    select_initial,
)
from overlay.app.subtitle_modes import (
    sub_tracks as _sub_tracks,
)
from overlay.app.subtitle_providers import (
    ProviderContext,
    SubtitleProvider,
    enabled_providers_for,
    fetch_first,
    get_provider,
    register_provider,
)

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubtitleCandidate:
    """One pickable subtitle source, provider-agnostic (Window 1). ``download`` is an off-thread thunk
    (no mpv IPC) that fetches + caches the file and returns ``(path, status)`` — the picker runs it
    through the normal subtitle-fetch pipeline, so it never needs to know which provider produced it."""

    provider: str
    name: str
    size: int
    match: bool  # release/resolution match hint for this encode
    download: Callable[[], tuple[Path | None, str]]


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


def _subtitle_identity(
    video: str, title_override: str | None, episode: int | None
) -> tuple[Path, str, int | None]:
    from overlay.app.jimaku import parse_filename

    video_path = Path(video)
    parsed_title, parsed_episode = parse_filename(video_path)
    return (
        video_path,
        title_override or parsed_title,
        episode if episode is not None else parsed_episode,
    )


def _cached_subtitle(
    video_path: Path, title: str, episode: int | None, *, resync: bool
) -> tuple[Path | None, str | None]:
    from overlay.app.subtitle_cache import cached_subs

    hit = cached_subs(video_path, title, episode, resync=resync) if video_path.exists() else None
    if hit is not None:
        log.info("subtitle cache hit: %s", hit)
    status = f"subtitle cache: using {hit.name} for {title!r} ep {episode}" if hit else None
    return hit, status


def _finish_subtitle(
    video_path: Path,
    title: str,
    episode: int | None,
    sub_path: str | Path,
    *,
    resync: bool,
) -> Path:
    finished = Path(sub_path)
    if resync and video_path.exists():
        from overlay.app.resync import maybe_resync

        finished = maybe_resync(video_path, finished, enabled=True)
    if video_path.exists():
        from overlay.app.subtitle_cache import store_subs

        finished = store_subs(video_path, title, episode, finished, resync=resync)
    return finished


def fetch_jimaku_path(
    video: str,
    *,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
    resync: bool = True,
    force: bool = False,
) -> tuple[Path | None, str]:
    """Fetch and optionally resync without touching mpv IPC, so callers may run it off-thread.
    ``force`` skips the cache so a user-triggered retry re-fetches and re-syncs (overwriting a
    stale/mistimed cached srt) — the cache-hit path silently reuses a bad prior sync otherwise."""
    from overlay.app.jimaku import JimakuClient, JimakuError

    video_path, title, ep = _subtitle_identity(video, jimaku_title, episode)
    if not force:
        hit, cache_status = _cached_subtitle(video_path, title, ep, resync=resync)
        if hit is not None:
            assert cache_status is not None
            return hit, cache_status
    tmp = tempfile.mkdtemp(prefix="saitenka-jimaku-")
    try:
        sub_path = JimakuClient(jimaku_key).fetch(title, ep, tmp, video=video)
    except JimakuError as e:
        return None, f"jimaku failed: {e}"
    sub_path = _finish_subtitle(video_path, title, ep, sub_path, resync=resync)
    return Path(sub_path), f"jimaku: added {Path(sub_path).name} for {title!r} ep {ep}"


def download_candidate_path(
    video: str,
    candidate,
    *,
    jimaku_key: str | None = None,
    jimaku_title: str | None = None,
    episode: int | None = None,
) -> tuple[Path | None, str]:
    """Download ONE user-chosen jimaku file (Window 1's source picker) and cache it — deliberately
    WITHOUT resync. The picker exists so the user selects a natively co-timed source; auto-resync on a
    sparse reference mangles it, and ``Ctrl+Shift+T`` stays the per-file fallback if it still drifts.
    ``candidate`` is a :class:`~overlay.app.jimaku.JimakuFile`. Off-thread safe (no mpv IPC)."""
    from overlay.app.jimaku import JimakuClient, JimakuError

    video_path, title, ep = _subtitle_identity(video, jimaku_title, episode)
    tmp = tempfile.mkdtemp(prefix="saitenka-jimaku-")
    try:
        sub_path = JimakuClient(jimaku_key).download(candidate, tmp)
    except JimakuError as exc:
        return None, f"jimaku download failed: {exc}"
    if video_path.exists():
        from overlay.app.subtitle_cache import store_subs

        sub_path = store_subs(video_path, title, ep, sub_path, resync=False)
    return Path(sub_path), f"jimaku: added {Path(sub_path).name}"


def download_tsukihime_candidate_path(
    video: str,
    release,
    attachment,
    *,
    config: dict | None = None,
    title_override: str | None = None,
    episode: int | None = None,
) -> tuple[Path | None, str]:
    """Download ONE user-chosen TsukiHime (release, attachment) pair — no resync, like the jimaku
    picker path. ``release``/``attachment`` are TsukiHime dataclasses from ``episode_candidates``."""
    from overlay.app.tsukihime import (
        API_BASE,
        DEFAULT_RESULT_CAP,
        DEFAULT_TIMEOUT,
        TsukiHimeClient,
        TsukiHimeError,
    )

    cfg = config or {}
    video_path, title, ep = _subtitle_identity(video, title_override, episode)
    dest = tempfile.mkdtemp(prefix="saitenka-tsukihime-")
    try:
        client = TsukiHimeClient(
            api_base=cfg.get("api_base", API_BASE),
            timeout=float(cfg.get("timeout", DEFAULT_TIMEOUT)),
            result_cap=int(cfg.get("result_cap", DEFAULT_RESULT_CAP)),
        )
        sub_path = client.download_attachment(release, attachment, dest)
    except (TsukiHimeError, TypeError, ValueError) as exc:
        return None, f"tsukihime download failed: {exc}"
    if video_path.exists():
        from overlay.app.subtitle_cache import store_subs

        sub_path = store_subs(video_path, title, ep, sub_path, resync=False)
    return Path(sub_path), f"tsukihime: added {Path(sub_path).name}"


def _jimaku_download(
    video: str, jf, jimaku_key: str | None, title_override: str | None
) -> Callable[[], tuple[Path | None, str]]:
    return lambda: download_candidate_path(
        video, jf, jimaku_key=jimaku_key, jimaku_title=title_override
    )


def _jimaku_candidates(
    video: str, jimaku_key: str | None, title_override: str | None
) -> list[SubtitleCandidate]:
    from overlay.app.jimaku import JimakuClient, _resolution_match

    _video_path, title, episode = _subtitle_identity(video, title_override, None)
    return [
        SubtitleCandidate(
            provider="jimaku",
            name=jf.name,
            size=jf.size,
            match=_resolution_match(video, jf.name),
            download=_jimaku_download(video, jf, jimaku_key, title_override),
        )
        for jf in JimakuClient(jimaku_key).episode_files(title, episode, video=video)
    ]


def _tsukihime_download(
    video: str, release, attachment, config: dict | None, title_override: str | None
) -> Callable[[], tuple[Path | None, str]]:
    return lambda: download_tsukihime_candidate_path(
        video, release, attachment, config=config, title_override=title_override
    )


def _tsukihime_candidates(
    video: str, config: dict | None, title_override: str | None
) -> tuple[list[SubtitleCandidate], list[str]]:
    from overlay.app.jimaku import _resolution_match
    from overlay.app.tsukihime import (
        API_BASE,
        DEFAULT_RESULT_CAP,
        DEFAULT_TIMEOUT,
        TsukiHimeClient,
    )

    cfg = config or {}
    _video_path, title, episode = _subtitle_identity(video, title_override, None)
    client = TsukiHimeClient(
        api_base=cfg.get("api_base", API_BASE),
        timeout=float(cfg.get("timeout", DEFAULT_TIMEOUT)),
        result_cap=int(cfg.get("result_cap", DEFAULT_RESULT_CAP)),
    )
    pairs, truncated = client.episode_candidates(title, episode)
    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"tsukihime: search truncated at {client.result_cap} — some releases may be missing"
        )
    out = [
        SubtitleCandidate(
            provider="tsukihime",
            name=f"{release.name}{attachment.extension}",
            size=0,  # TsukiHime attachments don't expose a size
            match=_resolution_match(video, release.name),
            download=_tsukihime_download(video, release, attachment, config, title_override),
        )
        for release, attachment in pairs
    ]
    return out, warnings


def _jimaku_provider_candidates(
    video: str, ctx: ProviderContext
) -> tuple[list[SubtitleCandidate], list[str]]:
    return _jimaku_candidates(video, ctx.jimaku_key, ctx.title_override), []


def _jimaku_provider_fetch(
    video: str, ctx: ProviderContext
) -> Callable[[], tuple[Path | None, str]]:
    return lambda: fetch_jimaku_path(
        video,
        jimaku_key=ctx.jimaku_key,
        jimaku_title=ctx.title_override,
        episode=ctx.episode,
        resync=ctx.resync,
        force=ctx.force,
    )


def _tsukihime_provider_candidates(
    video: str, ctx: ProviderContext
) -> tuple[list[SubtitleCandidate], list[str]]:
    return _tsukihime_candidates(video, ctx.tsukihime_config, ctx.title_override)


def _tsukihime_provider_fetch(
    video: str, ctx: ProviderContext
) -> Callable[[], tuple[Path | None, str]]:
    return lambda: fetch_tsukihime_path(
        video,
        config=ctx.tsukihime_config,
        title_override=ctx.title_override,
        episode=ctx.episode,
        resync=ctx.resync,
        force=ctx.force,
    )


# Both providers are Japanese-only today — capability, not a branch, so a future non-JP profile
# excludes them without touching this module (#254 phase 1).
register_provider(
    SubtitleProvider(
        name="jimaku",
        languages=frozenset({"jp"}),
        candidates=_jimaku_provider_candidates,
        fetch_attempt=_jimaku_provider_fetch,
    )
)
register_provider(
    SubtitleProvider(
        name="tsukihime",
        languages=frozenset({"jp"}),
        candidates=_tsukihime_provider_candidates,
        fetch_attempt=_tsukihime_provider_fetch,
    )
)


def list_candidates(
    video: str,
    providers: tuple[str, ...],
    *,
    jimaku_key: str | None = None,
    title_override: str | None = None,
    tsukihime_config: dict | None = None,
) -> tuple[list[SubtitleCandidate], list[str]]:
    """Aggregate pickable subtitle candidates across the enabled providers (Window 1). Returns
    ``(candidates, warnings)``; a provider that raises contributes a warning row instead of an
    exception, so one dead provider never blanks the panel. No mpv IPC — off-thread safe."""
    ctx = ProviderContext(
        jimaku_key=jimaku_key, title_override=title_override, tsukihime_config=tsukihime_config
    )
    candidates: list[SubtitleCandidate] = []
    warnings: list[str] = []
    for provider in providers:
        entry = get_provider(provider)
        if entry is None:
            continue
        try:
            found, warn = entry.candidates(video, ctx)
            candidates.extend(found)
            warnings.extend(warn)
        except Exception as exc:  # noqa: BLE001  # any provider failure → a soft warning, never fatal
            warnings.append(f"{provider}: {exc}")
    return candidates, warnings


def fetch_tsukihime_path(
    video: str,
    *,
    config: dict | None = None,
    title_override: str | None = None,
    episode: int | None = None,
    resync: bool = True,
    force: bool = False,
    dest_dir: str | Path | None = None,
) -> tuple[Path | None, str]:
    """Fetch a unique TsukiHime match without touching mpv IPC. ``force`` skips the cache (see
    :func:`fetch_jimaku_path`)."""
    from overlay.app.tsukihime import (
        API_BASE,
        DEFAULT_RESULT_CAP,
        DEFAULT_TIMEOUT,
        TsukiHimeClient,
        TsukiHimeError,
    )

    cfg = config or {}
    video_path, title, requested_episode = _subtitle_identity(video, title_override, episode)
    if not force:
        hit, cache_status = _cached_subtitle(video_path, title, requested_episode, resync=resync)
        if hit is not None:
            assert cache_status is not None
            return hit, cache_status
    destination = dest_dir or tempfile.mkdtemp(prefix="saitenka-tsukihime-")
    try:
        client = TsukiHimeClient(
            api_base=cfg.get("api_base", API_BASE),
            timeout=float(cfg.get("timeout", DEFAULT_TIMEOUT)),
            result_cap=int(cfg.get("result_cap", DEFAULT_RESULT_CAP)),
        )
        sub_path = client.fetch(title, requested_episode, destination)
    except (TsukiHimeError, TypeError, ValueError) as exc:
        return None, f"tsukihime failed: {exc}"
    sub_path = _finish_subtitle(video_path, title, requested_episode, sub_path, resync=resync)
    return (
        Path(sub_path),
        f"tsukihime: added {Path(sub_path).name} for {title!r} ep {requested_episode}",
    )


def fetch_provider_path(
    video: str,
    providers: tuple[str, ...],
    *,
    jimaku_key: str | None = None,
    title_override: str | None = None,
    episode: int | None = None,
    resync: bool = True,
    force: bool = False,
    tsukihime_config: dict | None = None,
) -> tuple[Path | None, str]:
    """Run configured providers in deterministic order without touching playback. ``force`` skips the
    cache so a user retry re-fetches + re-syncs (see :func:`fetch_jimaku_path`)."""
    ctx = ProviderContext(
        jimaku_key=jimaku_key,
        title_override=title_override,
        tsukihime_config=tsukihime_config,
        episode=episode,
        resync=resync,
        force=force,
    )
    attempts = [
        (provider, entry.fetch_attempt(video, ctx))
        for provider in providers
        if (entry := get_provider(provider)) is not None
    ]
    return fetch_first(attempts)


@dataclass(frozen=True)
class ProviderConfig:
    """Subtitle-source configuration threaded through ``run`` + ``attach``: which providers are enabled,
    the jimaku credentials/overrides, the resync toggle, and the attach-only initial-selection flags.

    One object so a new knob is a field, not another positional threaded through every fetch / retry /
    picker / re-slot function — the data-clump the ep-advance report exposed. ``run`` selects its track
    via its own launch flags, so it leaves the ``slang``/``jimaku``/``jimaku_force``/``tsukihime``
    selection fields at defaults and populates only the fetch fields; ``attach`` sets all of them. Per-
    episode overrides (``jimaku_title``/``episode``) are refreshed with :func:`dataclasses.replace` on a
    re-slot, since the title/episode come from the newly-loaded filename."""

    enabled_providers: tuple[str, ...] = ()
    jimaku_key: str | None = None
    jimaku_title: str | None = None
    episode: int | None = None
    resync: bool = True
    tsukihime_config: dict | None = None
    # attach-only initial-selection flags (run controls selection via its launch --slang/--sub-file)
    slang: str = "ja,jpn,jp"
    jimaku: bool = False
    jimaku_force: bool = False
    tsukihime: bool = False


def provider_fetch_factory(
    providers: tuple[str, ...], cfg: ProviderConfig, *, force: bool = False
) -> Callable[[str], Callable[[], tuple[Path | None, str]]]:
    """``factory(video) -> deferred fetch thunk`` — the one provider-fetch closure both ``run`` and
    ``attach`` hand to ``fetch_japanese_subs_async`` / ``configure_subtitle_retry``. ``providers`` is a
    per-call subset (startup vs retry vs background differ); the rest comes from ``cfg``. ``force=True``
    re-fetches + re-syncs past a stale/mistimed cached srt (the manual retry)."""

    def factory(video: str) -> Callable[[], tuple[Path | None, str]]:
        return lambda: fetch_provider_path(
            video,
            providers,
            jimaku_key=cfg.jimaku_key,
            title_override=cfg.jimaku_title,
            episode=cfg.episode,
            resync=cfg.resync,
            force=force,
            tsukihime_config=cfg.tsukihime_config,
        )

    return factory


def configure_providers(reader, cfg: ProviderConfig) -> None:
    """Wire the runtime provider surfaces shared by ``run`` and ``attach``: the manual re-sync retry (a
    force re-fetch) and the ``Ctrl+J`` source picker. Clears the retry to ``None`` when no provider is
    enabled, so a config with providers off can't leave a stale factory bound after a re-slot."""
    reader.configure_subtitle_retry(
        provider_fetch_factory(cfg.enabled_providers, cfg, force=True)
        if cfg.enabled_providers
        else None
    )
    if cfg.enabled_providers:
        reader.configure_sub_picker(
            lambda video: list_candidates(
                video,
                cfg.enabled_providers,
                jimaku_key=cfg.jimaku_key,
                title_override=cfg.jimaku_title,
                tsukihime_config=cfg.tsukihime_config,
            )
        )


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


def ensure_jp_subs(ipc, opts: AttachSubtitleOptions) -> str:
    """Make Japanese subtitles active on an attached mpv, mirroring ``run``'s precedence:
    explicit file > existing JP track > jimaku fetch. ``jimaku_force`` flips jimaku AHEAD of the
    embedded track (for files whose baked-in JP subs are mistimed/wrong), falling back to the embedded
    track only if the fetch fails. Hides mpv's native sub rendering whenever it takes control. Returns
    a human-readable status line for the CLI to print. (``opts.tsukihime`` is a deferred-fetch provider
    choice handled by the caller, not here.)"""
    if opts.sub_file:
        _add_and_select(ipc, Path(opts.sub_file).expanduser())
        ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format
        return f"using sub file {Path(opts.sub_file).name}"

    if opts.jimaku and opts.jimaku_force:
        ok, status = fetch_jimaku(
            ipc,
            jimaku_key=opts.jimaku_key,
            jimaku_title=opts.jimaku_title,
            episode=opts.episode,
            resync=opts.resync,
        )
        if ok:
            return status
        log.warning("jimaku force fetch failed (%s) — falling back to the embedded track", status)

    sid = select_sub_track(ipc, opts.slang)
    if sid is not None:
        ipc.command("set_property", "sub-visibility", False)  # noqa: FBT003  # mpv IPC passthrough — args ARE mpv's command wire format
        return f"selected JP subtitle track sid={sid}"

    if not opts.jimaku:
        return "no Japanese subtitle track found (pass --jimaku to fetch, or --sub-file)"

    _, status = fetch_jimaku(
        ipc,
        jimaku_key=opts.jimaku_key,
        jimaku_title=opts.jimaku_title,
        episode=opts.episode,
        resync=opts.resync,
    )
    return status


def remove_external_sub_tracks(ipc) -> int:
    """Drop every external subtitle track (return the count) — shared by the run and attach re-slots
    before re-adding the current episode's srt. mpv carries a prior episode's external over a playlist
    advance AND auto-selects it, so on advance the overlay would show the old episode's lines (found
    live: ep2's srt selected on ep03 as "unknown language 10/11"). Ids are stable across removals."""
    data = ipc.command("get_property", "track-list").get("data") or []
    removed = 0
    for track in data:
        if track.get("type") == "sub" and track.get("external") and track.get("id") is not None:
            log.info(
                "re-slot: dropping carried-over external sub sid=%s %r",
                track["id"],
                track.get("external-filename"),
            )
            ipc.command("sub-remove", track["id"])
            removed += 1
    return removed


@dataclass(frozen=True)
class AttachSubtitleOptions:
    """An ``attach``'s subtitle-sourcing choices — the attach analog of ``cli_run.RunSubtitleOptions``,
    resolved once from CLI/config and passed to :func:`prepare_attach_startup` (was a nine-arg clump)."""

    slang: str = "ja,jpn,jp"
    sub_file: str | None = None
    jimaku: bool = False
    jimaku_force: bool = False
    jimaku_key: str | None = None
    jimaku_title: str | None = None
    tsukihime: bool = False
    episode: int | None = None
    resync: bool = True
    language: str = MAIN_LANG  # active profile's main language — gates provider eligibility


def prepare_attach_startup(ipc, opts: AttachSubtitleOptions):
    """Select the immediate attach track and defer a missing-JP provider fetch."""
    status = ""
    if opts.sub_file or opts.jimaku_force:
        status = ensure_jp_subs(ipc, opts)

    startup = select_initial(ipc, opts.slang)
    if not status:
        if startup.active == MAIN_LANG:
            status = f"selected JP subtitle track sid={startup.tracks.jp_sid}"
        elif startup.active == SECOND_LANG:
            status = f"selected English fallback sid={startup.tracks.en_sid}"
        else:
            status = "no Japanese or English subtitle track found"
    # Same registry/language gate as the retry+picker enablement (cli.py) — one source of truth for
    # "which providers are on", so a non-jp profile can't leave this initial fetch chasing jimaku while
    # the picker excludes it. ``jimaku_force`` already fetched ahead in ensure_jp_subs, so it's excluded
    # from the deferred list here.
    providers: tuple[str, ...] = ()
    if startup.tracks.jp_sid is None:
        providers = enabled_providers_for(
            opts.language,
            (("jimaku", opts.jimaku and not opts.jimaku_force), ("tsukihime", opts.tsukihime)),
        )
    return startup, status, providers
