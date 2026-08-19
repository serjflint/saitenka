from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import cyclopts

from saitenka import otel_metrics
from saitenka.app.config import TooltipOptions, load_config
from saitenka.app.embedded_subs import build_sub_index_for_current_track
from saitenka.app.subselect import ProviderConfig

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from saitenka.app.config import ReaderOptions


def _build_attach_options(cfg: dict, *, mine: dict) -> ReaderOptions:
    from saitenka.app.config import (
        KeyOptions,
        MiningOptions,
        PanelOptions,
        PerfOptions,
        ReaderOptions,
        StatsOptions,
        TranslationOptions,
        subtitle_geometry_options,
    )

    ko, tt, mo, po = KeyOptions(), TooltipOptions(), MiningOptions(), PerfOptions()
    raw_stats = cfg.get("stats")
    stats: dict = raw_stats if isinstance(raw_stats, dict) else {}
    return ReaderOptions(
        keys=KeyOptions(
            mine_key=mine.get("key", ko.mine_key),
            mine_video_key=mine.get("video_key", ko.mine_video_key),
            mine_all_key=mine.get("all_key", ko.mine_all_key),
            preview_key=mine.get("preview_key", ko.preview_key),
            translate_key=cfg.get("translate_key", ko.translate_key),
            overlay_toggle_key=cfg.get("overlay_toggle_key", ko.overlay_toggle_key),
            hover_pause_key=cfg.get("hover_pause_key", ko.hover_pause_key),
            subtitle_language_key=cfg.get("subtitle_language_key", ko.subtitle_language_key),
            bookmark_key=cfg.get("bookmark_key", ko.bookmark_key),
            sidebar_key=cfg.get("sidebar_key", ko.sidebar_key),
            analysis_key=cfg.get("analysis_key", ko.analysis_key),
            annotation_key=cfg.get("annotation_key", ko.annotation_key),
            help_key=cfg.get("help_key", ko.help_key),
            profile_cycle_key=cfg.get("profile_cycle_key", ko.profile_cycle_key),
            subtitle_retry_key=cfg.get("subtitle_retry_key", ko.subtitle_retry_key),
            sub_prev_key=cfg.get("sub_prev_key", ko.sub_prev_key),
            sub_next_key=cfg.get("sub_next_key", ko.sub_next_key),
            sub_replay_key=cfg.get("sub_replay_key", ko.sub_replay_key),
        ),
        tooltip=TooltipOptions(
            tip_max_frac=cfg.get("tip_height", tt.tip_max_frac),
            tip_scale=cfg.get("tip_scale", tt.tip_scale),
            nested_max_frac=cfg.get("nested_max_frac", tt.nested_max_frac),
            pause_on_tooltip=bool(cfg.get("pause_on_tooltip", tt.pause_on_tooltip)),
            annotation_mode=cfg.get("annotation_mode", tt.annotation_mode),
            scan_delay=cfg.get("scan_delay", tt.scan_delay),
            hide_delay=cfg.get("hide_delay", tt.hide_delay),
            flash_secs=cfg.get("flash_secs", tt.flash_secs),
            panel_cache_max=cfg.get("panel_cache_max", tt.panel_cache_max),
            layout_engine=cfg.get("layout_engine", tt.layout_engine),
            render_cache=bool(cfg.get("render_cache", tt.render_cache)),
            mask_atlas=bool(cfg.get("mask_atlas", tt.mask_atlas)),
            render_cache_max_mb=cfg.get("render_cache_max_mb", tt.render_cache_max_mb),
            render_cache_min_height=cfg.get("render_cache_min_height", tt.render_cache_min_height),
        ),
        mining=MiningOptions(
            play_audio=not bool(cfg.get("no_audio_play")),
            show_preview=bool(mine.get("preview", mo.show_preview)),
            max_bulk=cfg.get("max_bulk", mo.max_bulk),
            anki_ok_ttl=cfg.get("anki_ok_ttl", mo.anki_ok_ttl),
            anki_ping_timeout=cfg.get("anki_ping_timeout", mo.anki_ping_timeout),
        ),
        translation=TranslationOptions(auto_translate=bool(cfg.get("auto_translate"))),
        stats=StatsOptions(
            enabled=bool(stats.get("enabled")),
            summary=bool(stats.get("summary", True)),
        ),
        panels=PanelOptions(scale=float(cfg.get("ui_scale", 1.0))),
        perf=PerfOptions(
            poll_interval=cfg.get("poll_interval", po.poll_interval),
            prefetch_workers=cfg.get("prefetch_workers", po.prefetch_workers),
            prefetch_lookahead=cfg.get("prefetch_lookahead", po.prefetch_lookahead),
            head_prefetch_lookahead=cfg.get("head_prefetch_lookahead", po.head_prefetch_lookahead),
            head_prefetch_queue_max=cfg.get("head_prefetch_queue_max", po.head_prefetch_queue_max),
        ),
        subtitle_geometry=subtitle_geometry_options(cfg),
        overlay_id_base=int(cfg.get("overlay_id_base", 1)),
    )


def _finish_attach_subtitle_startup(
    reader, ipc, startup, cfg: ProviderConfig, *, fetch_in_background: tuple[str, ...]
) -> None:
    if startup is not None:
        with otel_metrics.traced("startup.subtitle_mode_configure"):
            reader.configure_subtitle_mode(startup, slang=cfg.slang)
    with otel_metrics.traced("startup.subtitle_index"):
        build_sub_index_for_current_track(reader)
    from saitenka.app.subselect import configure_providers, provider_fetch_factory

    configure_providers(reader, cfg)  # shared with run: manual re-sync retry + Ctrl+J source picker
    if not fetch_in_background:
        return
    video_path = ipc.command("get_property", "path").get("data")
    if not video_path:
        return
    background_fetch = provider_fetch_factory(fetch_in_background, cfg)
    reader.fetch_japanese_subs_async(background_fetch(str(video_path)))


def _attach_reslot(reader, ipc, path: Path, cfg: ProviderConfig) -> None:
    """Re-establish Japanese subs when the user's mpv advances to the next episode in ATTACH mode
    (#100). Reactive only — fired from mpv's ``file-loaded`` (attach never sets ``advance_hook``; the
    user/SyncPlay owns playback, the #62 gate). Closes the finished stats row, rebinds the leak-free
    EpisodeContext, drops the carried-over external sub, re-runs the attach selection (which prefers JP
    and defers a jimaku fetch when the new file has none — so watching continues in Japanese even when
    the next episode ships no JP track), re-wires the retry/picker, and restarts recorder + prefetch."""
    from dataclasses import replace

    from saitenka import otel_metrics
    from saitenka.app import session_stats
    from saitenka.app.jimaku import parse_filename
    from saitenka.app.subselect import (
        AttachSubtitleOptions,
        prepare_attach_startup,
        remove_external_sub_tracks,
    )

    title, episode = parse_filename(path)
    ep_cfg = replace(
        cfg, jimaku_title=title, episode=episode
    )  # per-episode overrides from filename
    startup = None
    status = ""
    fetch_background: tuple[str, ...] = ()
    with otel_metrics.traced("subtitle.reslot") as span:
        span.set("mode", "attach")
        reader.finish_session_stats()  # close the finished episode's row before the recorder resets
        reader.rebind_episode()
        span.set("externals_dropped", remove_external_sub_tracks(ipc))
        try:
            startup, status, fetch_background = prepare_attach_startup(
                ipc,
                AttachSubtitleOptions(
                    slang=ep_cfg.slang,
                    jimaku=ep_cfg.jimaku,
                    jimaku_force=ep_cfg.jimaku_force,
                    jimaku_key=ep_cfg.jimaku_key,
                    jimaku_title=title,
                    tsukihime=ep_cfg.tsukihime,
                    episode=episode,
                    resync=ep_cfg.resync,
                ),
            )
        except Exception:  # never let sub selection break following the advance
            log.warning("attach re-slot sub selection failed", exc_info=True)
        _finish_attach_subtitle_startup(
            reader, ipc, startup, ep_cfg, fetch_in_background=fetch_background
        )
        session_stats.start(reader)  # fresh row; identity read from mpv's now-current path
        reader.start_prefetch()  # lookahead workers re-key onto the new episode's sub-index
        span.set("active", (startup.active if startup else None) or "none")
    log.info("attach re-slot onto %s: %s", path.name, status or "no subtitle selection")


def _install_attach_reslot_hook(reader, ipc, cfg: ProviderConfig) -> None:
    """#100 in attach: follow the user's mpv to the next episode (native autoload/playlist advance) and
    re-establish JP subs via :func:`_attach_reslot`, so watching continues in Japanese without a manual
    re-attach. Reactive only (``reslot_hook`` on ``file-loaded``) — attach never sets ``advance_hook``;
    the user/SyncPlay owns advancing (the #62 gate). No-op if mpv has no current path yet."""
    current_path = ipc.command("get_property", "path").get("data")
    if not current_path:
        return

    def _hook(loaded_path: Path) -> None:
        _attach_reslot(reader, ipc, loaded_path, cfg)

    reader.install_reslot_hook(_hook, initial=Path(str(current_path)))


def attach(  # noqa: PLR0913  # cyclopts CLI signature — each flag must stay an individual parameter
    socket: str | None = None,
    *,
    config: str | None = None,
    slang: Annotated[
        str, cyclopts.Parameter(help="preferred (JP) sub languages, priority order")
    ] = "ja,jpn,jp",
    sub_file: Annotated[
        str | None, cyclopts.Parameter(help="external subtitle file to add + select")
    ] = None,
    jimaku: Annotated[
        bool, cyclopts.Parameter(negative=(), help="fetch JP subs from jimaku.cc when none present")
    ] = False,
    jimaku_force: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="force jimaku.cc subs AHEAD of the embedded JP track (for mistimed/wrong baked-in "
            "subs); falls back to the embedded track if the fetch fails. Implies --jimaku",
        ),
    ] = False,
    jimaku_key: Annotated[
        str | None, cyclopts.Parameter(help="jimaku.cc API key (else $JIMAKU_API_KEY)")
    ] = None,
    jimaku_title: Annotated[
        str | None, cyclopts.Parameter(help="override the title parsed from the filename")
    ] = None,
    episode: Annotated[
        int | None, cyclopts.Parameter(help="override the episode parsed from the filename")
    ] = None,
    resync: Annotated[
        bool, cyclopts.Parameter(negative="--no-resync", help="resync jimaku subs (default: on)")
    ] = True,
    profile: Annotated[
        str | None,
        cyclopts.Parameter(help="active reading profile name ([profiles.<name>] in the config)"),
    ] = None,
) -> (
    int
):  # pragma: no cover — connects to a live mpv; the reader loop is covered by controller tests
    """Attach to an already-running mpv's IPC socket instead of launching mpv.

    mpv accepts multiple concurrent IPC clients, so we JOIN a socket shared with
    mpv_websocket/animecards rather than take it over. On attach we actively select the Japanese
    subtitle track (the user's mpv may prefer English), fetching from jimaku when asked.
    """
    from saitenka.app.launch.run import setup_session_telemetry
    from saitenka.app.profiles import resolve_launch_identity
    from saitenka.app.reader_deps import begin_tokenizer_warm

    # The shared run/attach identity spine (#254): --profile override, active profile, scoped cfg,
    # effective slang, switcher cycle — resolved in ONE place so run and attach can't drift. attach has
    # no mining CLI flags, so `_mine_config_from(cfg["mine"])` picks up the profile's deck/model directly.
    ident = resolve_launch_identity(load_config(config), profile_override=profile, slang=slang)
    cfg, active_profile, slang, profile_cycle = (
        ident.cfg,
        ident.profile,
        ident.slang,
        ident.profile_cycle,
    )

    # Fire this as early as possible — before the IPC connect handshake — so fugashi's slow
    # first-ever tokenize() call (see warm_tokenizer's docstring) overlaps that dead time instead of
    # landing on the critical path later. Warms the ACTIVE profile's tokenizer (no-op for non-unidic).
    setup_session_telemetry(cfg)  # capture is per reader session, not global (see cli.main note)
    tokenizer_warm = begin_tokenizer_warm(active_profile.tokenizer)
    from saitenka.mpvio.ipc import MpvIPC, default_attach_ipc_path

    sock = socket or cfg.get("mpv_socket") or default_attach_ipc_path()
    if not sock:
        print(
            "no socket given — pass one (e.g. --attach /tmp/mpv-socket) or set mpv_socket in the "
            "config, or add `input-ipc-server=<path>` to mpv.conf",
            file=sys.stderr,
        )
        return 2

    # Step aside if SubMiner is running — it injects its own mpv overlay, and two overlays over one
    # video flicker / stick on "overlay loading". Quit SubMiner (or uninstall its plugin) to use this.
    from saitenka.app.conflicts import subminer_running

    if subminer_running():
        msg = "SubMiner is running — skipping the Saitenka overlay to avoid a double overlay. Quit SubMiner to use Saitenka."
        log.warning("attach: %s", msg)
        print(msg, file=sys.stderr, flush=True)
        return 0

    with otel_metrics.traced("startup.mpv_connect"):
        try:
            ipc = MpvIPC(sock).connect(timeout=15)
        except TimeoutError as e:
            print(f"could not attach to mpv IPC at {sock}: {e}", file=sys.stderr)
            return 2
    from saitenka.app.session_routes import install_session_runtime

    # No startup hint: attach joins an mpv that is already playing, and the breadcrumb exists to
    # cover a file-load wait that has already happened.
    install_session_runtime(ipc, startup_hint=False)

    from saitenka.app.subselect import AttachSubtitleOptions, prepare_attach_startup
    from saitenka.app.subtitle_providers import enabled_providers_for

    # [jimaku] config table feeds attach defaults so plugin mode (which spawns a bare `attach`) can
    # fetch subs without CLI flags. An explicit --jimaku / --jimaku-key still wins.
    _jm = cfg.get("jimaku")
    jm = _jm if isinstance(_jm, dict) else {}
    _th = cfg.get("tsukihime")
    th = _th if isinstance(_th, dict) else {}
    jimaku_force = jimaku_force or bool(jm.get("force", False))
    jimaku = (
        jimaku or jimaku_force or bool(jm.get("enabled", False) or jm.get("fetch", False))
    )  # force implies fetch
    jimaku_key = jimaku_key or jm.get("key")
    resync = resync and bool(jm.get("resync", True))
    enabled_providers = enabled_providers_for(
        active_profile.langs.main, (("jimaku", jimaku), ("tsukihime", bool(th.get("enabled"))))
    )

    subtitle_startup = None
    fetch_jimaku_in_background: tuple[str, ...] = ()
    try:
        with otel_metrics.traced("startup.subtitle_selection"):
            subtitle_startup, status, fetch_jimaku_in_background = prepare_attach_startup(
                ipc,
                AttachSubtitleOptions(
                    slang=slang,
                    sub_file=sub_file,
                    jimaku=jimaku,
                    jimaku_force=jimaku_force,
                    jimaku_key=jimaku_key,
                    jimaku_title=jimaku_title,
                    tsukihime=bool(th.get("enabled", False)),
                    episode=episode,
                    resync=resync,
                    language=active_profile.langs.main,
                ),
            )
        log.info("attach subs: %s", status)  # plugin mode is detached — the log is the only sink
        print("subs:", status, flush=True)
    except Exception as e:  # never let sub selection block the attach
        log.warning("attach sub selection failed", exc_info=True)
        print(
            f"subs: selection failed ({e}) — using mpv's current track", file=sys.stderr, flush=True
        )

    # Progressive startup: build the reader with NO coloring/dict/mining collaborators so plain
    # subtitles draw immediately, then load them in the BACKGROUND (dicts/scorer/anki — the slow
    # first-run cache build). A top-left spinner runs in the reader's own poll loop meanwhile; when the
    # load finishes, coloring + tooltips + mining light up in place. Dicts and Anki are both optional —
    # with none configured, attach stays a working subtitle renderer (jamdict-fallback tooltips).
    _mc = cfg.get("mine")
    mc = _mc if isinstance(_mc, dict) else {}
    opts = _build_attach_options(cfg, mine=mc)
    from saitenka.app.reader_factory import create_reader

    with otel_metrics.traced("startup.reader_create"):
        reader = create_reader(
            ipc,
            options=opts,
            profile=active_profile,
            tokenizer_warm=tokenizer_warm,
        )
    from saitenka.app.reader_deps import make_dict_scoper

    reader.set_profile_cycle(
        profile_cycle,
        make_dict_scoper(cfg) if len(profile_cycle) > 1 else None,
        base_slang=ident.base_slang,
    )
    provider_cfg = ProviderConfig(
        enabled_providers=enabled_providers,
        jimaku_key=jimaku_key,
        jimaku_title=jimaku_title,
        episode=episode,
        resync=resync,
        tsukihime_config=th,
        slang=slang,
        jimaku=jimaku,
        jimaku_force=jimaku_force,
        tsukihime=bool(th.get("enabled", False)),
    )
    _finish_attach_subtitle_startup(
        reader, ipc, subtitle_startup, provider_cfg, fetch_in_background=fetch_jimaku_in_background
    )
    _install_attach_reslot_hook(reader, ipc, provider_cfg)
    reader.load_deps_async(cfg)
    print(
        f"attached to mpv on {sock} — subs now; coloring/tooltips/mining load in the background. "
        "Ctrl+C to detach (mpv keeps running).",
        flush=True,
    )
    # Record the mode in the session log — attach/plugin vs run behave differently (async dep load,
    # other mpv scripts sharing input), so a report must say which one produced it.
    log.info("session: mode=attach socket=%s", sock)
    try:
        reader.run()
    finally:
        try:
            reader.close()
            ipc.close()
        except Exception:
            log.debug("attach shutdown cleanup failed", exc_info=True)
    return 0


def register(app: cyclopts.App) -> None:
    app.command(attach)
