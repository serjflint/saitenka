"""Wave-2 P3: the guts of ``cli.py``'s ``run`` command, extracted behind a stable seam.

``run`` stays a thin cyclopts-decorated forwarder in ``cli.py`` (its exact signature has to live
there for cyclopts to build ``--help``/parsing — see ``tests/test_cli.py``'s flag-inventory
contract); this module holds ``run_impl`` and the cohesive helpers it's built from, split out of
what used to be one ~350-line, CCN-147 function. Behavior is unchanged from the pre-split version.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from overlay.app.config import config_path, load_config
from overlay.app.embedded_subs import build_sub_index_for_current_track
from overlay.app.paths import cache_dir

log = logging.getLogger(__name__)

DEMO_LINE = "門前の小僧習わぬ経を読む"


def setup_session_telemetry(cfg: dict) -> None:
    """Stand up telemetry capture for THIS reader session (opt-in via ``[telemetry].enabled``). Scoped
    to run/attach on purpose — NOT ``cli.main`` — because a one-shot utility command (``report`` /
    ``doctor``) would otherwise start the CTF writer thread against the session's ``trace.json`` and
    truncate it on the writer's first counter sample (found live: a ``report`` right after a run
    clobbered a 1122-span trace down to one fresh-process counter row). Those commands only READ the
    on-disk trace, so they need no capture pipeline."""
    from overlay.app.config import resolve_telemetry
    from overlay.app.telemetry import configure, is_enabled

    configure(resolve_telemetry(cfg))
    if is_enabled():
        print(  # user-facing opt-in notice
            "[saitenka] telemetry: enabled (captures usage/perf data) "
            "— run `saitenka telemetry disable` to turn off"
        )


def _log_mpv_exit(code: int | None) -> None:
    """mpv's own crashes (e.g. a GPU-driver SIGSEGV) look identical to a clean quit from our side —
    the IPC socket just drops either way (``mpv IPC reader: EOF ... mpv closed the pipe``). Check the
    real exit code (``proc.returncode``) so a crash shows up in overlay.log/report instead of reading
    as a normal quit. No-op if we had to force-kill it (``kill_process_tree`` above) — that exit is
    ours, not mpv's."""
    if code is None or code == 0:
        return
    if code < 0:
        import signal

        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = str(-code)
        log.warning(
            "mpv exited abnormally: killed by signal %d (%s) — see ~/Library/Logs/"
            "DiagnosticReports (macOS) for a native crash report",
            -code,
            name,
        )
    else:
        log.warning("mpv exited with non-zero status %d", code)


DEMO_LINE_EN = "A shop-boy at the temple gate recites sutras he was never taught."


def _resolve_names(flag_vals: list[str] | None, cfg: dict, key: str) -> list[str]:
    """Flag values win over the config file. Values are dictionary **titles** resolved against the
    consolidated DB (imported once) — not paths, so no ~/$VAR expansion is needed."""
    return list(flag_vals or []) or list(cfg.get(key) or [])


def jimaku_should_fetch(
    *,
    explicit_flag: bool,
    cfg_fetch: bool,
    video: str | None,
    slang: str = "ja,jpn,jp",
    probe=None,
) -> bool:
    """Decide whether ``run`` fetches jimaku. Explicit ``--jimaku`` always wins. Config-driven fetch
    (``[jimaku].fetch``) fires ONLY when the file has no embedded JP subtitle track — so a global
    fetch=true doesn't override good embedded subs (matching what ``attach`` does over IPC). Unknown
    (can't probe) → fetch, since the point of a configured key is to provide subs."""
    if not video:  # no real file (demo/test clip) — nothing to fetch for
        return False
    if explicit_flag:
        return True
    if not cfg_fetch:
        return False
    if probe is None:
        from overlay.app.media import has_sub_lang as probe
    return probe(video, slang) is not True  # fetch unless a JP track is definitely present


def _make_clip(
    path: Path, seconds: int, w: int, h: int
) -> None:  # pragma: no cover — live-run entry point
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x18283a:size={w}x{h}:rate=30:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _make_srt(
    path: Path, seconds: int, line: str
) -> None:  # pragma: no cover — live-run entry point
    end = f"00:00:{seconds:02d},000"
    path.write_text(f"1\n00:00:00,000 --> {end}\n{line}\n", encoding="utf-8")


def _prepare_video(video: str | None, width: int, height: int, seconds: float) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix="saitenka-reader-"))
    dur = max(8, int(seconds))
    video_path = Path(video).expanduser() if video else tmp / "clip.mp4"
    if not video:
        print(f"no video — generating a {width}x{height} test clip…")
        _make_clip(video_path, dur, width, height)
    return tmp, video_path, dur


def _resolve_jimaku_subs(
    video_path: Path,
    jimaku_title: str | None,
    episode: int | None,
    jimaku_key: str | None,
    jimaku_cfg: dict,
    *,
    resync: bool,
) -> Path | None:
    from overlay.app.subselect import fetch_jimaku_path

    sub_path, status = fetch_jimaku_path(
        str(video_path),
        jimaku_key=jimaku_key or jimaku_cfg.get("key"),
        jimaku_title=jimaku_title,
        episode=episode,
        resync=resync,
    )
    print(status, file=sys.stderr if sub_path is None else sys.stdout)
    return sub_path


def _cached_subtitles(
    video_path: Path, jimaku_title: str | None, episode: int | None, *, resync: bool
) -> Path | None:
    from overlay.app.jimaku import parse_filename
    from overlay.app.subtitle_cache import cached_subs

    title, parsed_episode = parse_filename(video_path)
    title = jimaku_title or title
    episode = episode if episode is not None else parsed_episode
    hit = cached_subs(video_path, title, episode, resync=resync) if video_path.exists() else None
    if hit is not None:
        print("subtitle cache: using", hit.name)
        log.info("subtitle cache hit: %s", hit)
    return hit


def _enabled_provider_names(
    video: str | None, *, jimaku: bool, jimaku_cfg: dict, tsukihime_cfg: dict
) -> tuple[str, ...]:
    if not video:
        return ()
    flags = (
        ("jimaku", jimaku or bool(jimaku_cfg.get("fetch") or jimaku_cfg.get("enabled"))),
        ("tsukihime", bool(tsukihime_cfg.get("enabled"))),
    )
    return tuple(provider for provider, enabled in flags if enabled)


def _configured_subtitles(
    video_path: Path,
    jimaku_title: str | None,
    episode: int | None,
    *,
    jimaku: bool,
    tsukihime: bool,
    resync: bool,
) -> tuple[Path | None, tuple[str, ...]]:
    cached = _cached_subtitles(video_path, jimaku_title, episode, resync=resync)
    if cached is not None:
        return cached, ()
    providers = tuple(
        provider for provider, enabled in (("jimaku", jimaku), ("tsukihime", tsukihime)) if enabled
    )
    return None, providers


def _resolve_subtitles(
    cfg: dict,
    video: str | None,
    video_path: Path,
    dur: int,
    tmp: Path,
    *,
    sub_file: str | None,
    jimaku: bool,
    jimaku_key: str | None,
    jimaku_title: str | None,
    episode: int | None,
    resync: bool,
    slang: str,
) -> tuple[Path | None, Path | None, tuple[str, ...], tuple[str, ...]]:
    """Resolve explicit startup subtitles and defer configured providers until mpv is ready."""
    _jm = cfg.get("jimaku")
    jimaku_cfg = _jm if isinstance(_jm, dict) else {}
    jimaku_on = jimaku_should_fetch(
        explicit_flag=jimaku,
        cfg_fetch=bool(jimaku_cfg.get("fetch") or jimaku_cfg.get("enabled")),
        video=str(video_path) if video else None,
        slang=slang,
    )
    raw_tsukihime = cfg.get("tsukihime")
    tsukihime_cfg = raw_tsukihime if isinstance(raw_tsukihime, dict) else {}
    tsukihime_on = jimaku_should_fetch(
        explicit_flag=False,
        cfg_fetch=bool(tsukihime_cfg.get("enabled")),
        video=str(video_path) if video else None,
        slang=slang,
    )
    log.info(
        "jimaku fetch: %s (flag=%s configured=%s)",
        jimaku_on,
        jimaku,
        bool(jimaku_cfg.get("fetch") or jimaku_cfg.get("enabled")),
    )
    sub_path = en_sub_path = None
    enabled_providers = _enabled_provider_names(
        video, jimaku=jimaku, jimaku_cfg=jimaku_cfg, tsukihime_cfg=tsukihime_cfg
    )
    fetch_in_background: tuple[str, ...] = ()
    if sub_file:
        sub_path = Path(sub_file).expanduser()
    elif jimaku_on and jimaku:
        sub_path = _resolve_jimaku_subs(
            video_path, jimaku_title, episode, jimaku_key, jimaku_cfg, resync=resync
        )
        if sub_path is None and tsukihime_on:
            fetch_in_background = ("tsukihime",)
    elif jimaku_on or tsukihime_on:
        sub_path, fetch_in_background = _configured_subtitles(
            video_path,
            jimaku_title,
            episode,
            jimaku=jimaku_on,
            tsukihime=tsukihime_on,
            resync=resync,
        )
    elif not video:
        sub_path = tmp / "line.srt"
        _make_srt(sub_path, dur, DEMO_LINE)
        en_sub_path = tmp / "line.en.srt"  # secondary EN track → test the `t` translation reveal
        _make_srt(en_sub_path, dur, DEMO_LINE_EN)
    return sub_path, en_sub_path, fetch_in_background, enabled_providers


def _launch_mpv_and_connect(
    cfg: dict,
    tmp: Path,
    video_path: Path,
    *,
    slang: str,
    start: str,
    screenshot: str | None,
    sub_path,
    en_sub_path,
    use_config: bool,
    fullscreen: bool,
    mpv_arg: list[str] | None = None,
) -> tuple:
    """Find + launch mpv and connect its IPC socket. Returns ``(None, None)`` (having already
    printed the reason) when mpv can't be found or its IPC never comes up."""
    from overlay.mpvio.discover import find_mpv
    from overlay.mpvio.ipc import MpvIPC, default_ipc_path

    mpv_bin = find_mpv(cfg.get("mpv_path"))
    if not mpv_bin:
        print(
            "mpv not found — install it (Windows: `winget install shinchiro.mpv`; macOS: "
            "`brew install mpv`), or set `mpv_path` in overlay.toml. Run `saitenka doctor`.",
            file=sys.stderr,
        )
        return None, None
    # On Windows mpv IPC is a named pipe, not a filesystem socket — see default_ipc_path.
    sock = default_ipc_path(tmp.name)
    # Capture mpv's own log next to ours so `report` can bundle it — the mpv side (codec, sub load,
    # track select failures) is otherwise invisible in a bug report. Overwritten each run.
    mpv_log = cache_dir() / "mpv.log"
    from overlay.mpvio.launch import build_mpv_argv

    cmd = build_mpv_argv(
        mpv_bin,
        sock,
        mpv_log,
        video_path,
        slang=slang,
        start=start,
        screenshot=bool(screenshot),
        sub_path=sub_path,
        en_sub_path=en_sub_path,
        use_config=use_config,
        fullscreen=fullscreen,
        extra_args=mpv_arg,
    )
    print("launching:", " ".join(cmd))
    log.info("launching mpv: %s", " ".join(cmd))  # capture the exact flags in the bundle-able log
    proc = subprocess.Popen(cmd)

    try:
        ipc = MpvIPC(sock).connect(timeout=15)
    except TimeoutError as e:
        print("mpv IPC unreachable:", e, file=sys.stderr)
        from overlay.app.procutil import kill_process_tree

        kill_process_tree(proc)
        return None, None
    return proc, ipc


def _build_run_options(
    cfg: dict,
    *,
    mine_key: str,
    mine_all_key: str,
    translate_key: str,
    preview_key: str,
    tip_height: float,
    pause_on_tooltip: bool,
    hover_switch_delay: float,
    no_audio_play: bool,
    mine_preview: bool,
    auto_translate: bool,
    prefetch: bool,
):
    from overlay.app.config import (
        KeyOptions,
        MiningOptions,
        PanelOptions,
        PerfOptions,
        ReaderOptions,
        StatsOptions,
        TooltipOptions,
        TranslationOptions,
    )

    _ko, _tt, _mo, _po = KeyOptions(), TooltipOptions(), MiningOptions(), PerfOptions()
    raw_stats = cfg.get("stats")
    stats: dict = raw_stats if isinstance(raw_stats, dict) else {}
    return ReaderOptions(
        keys=KeyOptions(
            mine_key=mine_key,
            mine_all_key=mine_all_key,
            translate_key=translate_key,
            overlay_toggle_key=cfg.get("overlay_toggle_key", _ko.overlay_toggle_key),
            preview_key=preview_key,
            hover_pause_key=cfg.get("hover_pause_key", _ko.hover_pause_key),
            subtitle_language_key=cfg.get("subtitle_language_key", _ko.subtitle_language_key),
            bookmark_key=cfg.get("bookmark_key", _ko.bookmark_key),
            sidebar_key=cfg.get("sidebar_key", _ko.sidebar_key),
            analysis_key=cfg.get("analysis_key", _ko.analysis_key),
            annotation_key=cfg.get("annotation_key", _ko.annotation_key),
            help_key=cfg.get("help_key", _ko.help_key),
            subtitle_retry_key=cfg.get("subtitle_retry_key", _ko.subtitle_retry_key),
            sub_prev_key=cfg.get("sub_prev_key", "Alt+LEFT"),
            sub_next_key=cfg.get("sub_next_key", "Alt+RIGHT"),
            sub_replay_key=cfg.get("sub_replay_key", "Alt+DOWN"),
        ),
        tooltip=TooltipOptions(
            tip_max_frac=tip_height,
            nested_max_frac=cfg.get("nested_max_frac", _tt.nested_max_frac),
            pause_on_tooltip=pause_on_tooltip,
            annotation_mode=cfg.get("annotation_mode", _tt.annotation_mode),
            hover_switch_delay=hover_switch_delay,
            scan_delay=cfg.get("scan_delay", _tt.scan_delay),
            hide_delay=cfg.get("hide_delay", _tt.hide_delay),
            flash_secs=cfg.get("flash_secs", _tt.flash_secs),
            panel_cache_max=cfg.get("panel_cache_max", _tt.panel_cache_max),
        ),
        mining=MiningOptions(
            play_audio=not no_audio_play,
            show_preview=mine_preview,
            max_bulk=cfg.get("max_bulk", _mo.max_bulk),
            anki_ok_ttl=cfg.get("anki_ok_ttl", _mo.anki_ok_ttl),
            anki_ping_timeout=cfg.get("anki_ping_timeout", _mo.anki_ping_timeout),
        ),
        translation=TranslationOptions(auto_translate=auto_translate),
        stats=StatsOptions(
            enabled=bool(stats.get("enabled", False)),
            summary=bool(stats.get("summary", True)),
        ),
        panels=PanelOptions(scale=float(cfg.get("ui_scale", 1.0))),
        perf=PerfOptions(
            poll_interval=cfg.get("poll_interval", _po.poll_interval),
            prefetch_workers=cfg.get("prefetch_workers", _po.prefetch_workers),
            prefetch_lookahead=cfg.get("prefetch_lookahead", _po.prefetch_lookahead),
            head_prefetch_lookahead=cfg.get("head_prefetch_lookahead", _po.head_prefetch_lookahead),
            head_prefetch_queue_max=cfg.get("head_prefetch_queue_max", _po.head_prefetch_queue_max),
        ),
        prefetch=prefetch,
    )


def _provider_fetch_factory(
    providers: tuple[str, ...],
    *,
    jimaku_key: str | None,
    jimaku_title: str | None,
    episode: int | None,
    resync: bool,
    tsukihime_cfg: dict,
):
    from overlay.app.subselect import fetch_provider_path

    def factory(video: str):
        return lambda: fetch_provider_path(
            video,
            providers,
            jimaku_key=jimaku_key,
            title_override=jimaku_title,
            episode=episode,
            resync=resync,
            tsukihime_config=tsukihime_cfg,
        )

    return factory


def _start_run_provider_fetch(
    reader,
    cfg: dict,
    video_path: Path,
    *,
    providers: tuple[str, ...],
    enabled_providers: tuple[str, ...],
    jimaku_title: str | None,
    episode: int | None,
    jimaku_key: str | None,
    resync: bool,
) -> None:
    if not providers and not enabled_providers:
        return
    raw_tsukihime = cfg.get("tsukihime")
    tsukihime_cfg = raw_tsukihime if isinstance(raw_tsukihime, dict) else {}
    retry_factory = _provider_fetch_factory(
        enabled_providers,
        jimaku_key=jimaku_key,
        jimaku_title=jimaku_title,
        episode=episode,
        resync=resync,
        tsukihime_cfg=tsukihime_cfg,
    )
    reader.configure_subtitle_retry(retry_factory if enabled_providers else None)
    if providers:
        startup_factory = _provider_fetch_factory(
            providers,
            jimaku_key=jimaku_key,
            jimaku_title=jimaku_title,
            episode=episode,
            resync=resync,
            tsukihime_cfg=tsukihime_cfg,
        )
        reader.fetch_japanese_subs_async(startup_factory(str(video_path)))


def _build_run_deps(
    *,
    mine: bool,
    mine_deck: str,
    mine_model: str,
    mine_key: str,
    mine_all_key: str,
    mine_normalize_audio: bool,
    known_cfg,
    known: str,
    color: bool,
    dict_titles: list[str],
    freq_titles: list[str],
    pitch_titles: list[str],
):
    """Build the coloring/dict/mining collaborators. This is the slow part (the first-run
    dictionary cache build is 25-66s per dict), so ``run_impl`` defers calling this to a BACKGROUND
    thread (see ``reader.load_deps_async``) unless a demo/screenshot needs it synchronously. Must
    NOT touch the mpv IPC (it can run off the main thread).

    Delegates to :func:`reader_deps.build_reader_deps` — the parallelized (ThreadPoolExecutor)
    implementation `attach` already uses — instead of keeping a second, sequential copy of the same
    dict/freq/JLPT/known-words/mining logic (a prior copy here silently drifted out of sync with
    `attach`'s, undoing an optimization made only on that side). Only the CLI-specific bits stay
    here: the plain ``--known word1,word2`` fallback list and this command's console feedback
    lines, both threaded through as callbacks/post-processing rather than duplicating the logic
    that produces them."""
    from overlay.app import reader_deps

    def _on_anki_unreachable() -> None:
        print(
            "note: Anki/AnkiConnect not reachable — start Anki (with the AnkiConnect "
            "add-on). Coloring falls back to freq+JLPT; mining is unavailable until it's up.",
            file=sys.stderr,
        )

    def _on_known_words_error(e: Exception) -> None:
        print(
            f"known-word load from Anki failed ({e}) — coloring by freq+JLPT only", file=sys.stderr
        )

    effective_cfg = {
        "dicts": dict_titles,
        "freq": freq_titles,
        "pitch": pitch_titles,
        "known": known_cfg,
        "mine": {"deck": mine_deck, "model": mine_model, "normalize_audio": mine_normalize_audio}
        if mine
        else {},
    }
    scorer, anki, mine_conf, dict_set = reader_deps.build_reader_deps(
        effective_cfg,
        color=color,
        mine=mine,
        known_words=known,
        on_anki_unreachable=_on_anki_unreachable,
        on_known_words_error=_on_known_words_error,
    )

    if not mine:
        log.info("mining disabled (no [mine] config / --no-mine)")
    elif anki is not None:
        print(
            f"mining on — {mine_key} mine · {mine_all_key or 'Shift+m'} mine-all "
            f"→ {mine_deck} ({mine_model})"
        )
        log.info("mining enabled: deck=%r model=%r key=%r", mine_deck, mine_model, mine_key)

    if dict_set is not None:
        print(
            f"dictionaries: {len(dict_set.dicts)} defn, {len(dict_set.freqs)} freq, "
            f"{len(dict_set.pitches)} pitch — see {config_path()} to change"
        )
        log.info(
            "dictionaries loaded: %d defn, %d freq, %d pitch",
            len(dict_set.dicts),
            len(dict_set.freqs),
            len(dict_set.pitches),
        )

    if scorer is not None:
        print(f"coloring on — known:{len(scorer.known.words)} freq:{bool(scorer.freq)} jlpt:on")

    return scorer, anki, mine_conf, dict_set


def _wait_for_subtitle_text(reader, ipc, video: str | None) -> str:
    """Sample sub-text; on a real file with nothing showing yet, hop forward via sub-seek until one
    lands (or 80 tries elapse). Falls back to DEMO_LINE if nothing ever appears."""
    reader.refresh_osd()
    text = reader._get("sub-text") or ""
    if not text and video:  # real file: hop to the next subtitle cue
        for _ in range(80):
            ipc.command("sub-seek", 1)
            time.sleep(0.12)
            text = reader._get("sub-text") or ""
            if text:
                break
    return text or DEMO_LINE


def _run_demo_actions(
    reader,
    ipc,
    *,
    demo_scroll: int,
    demo_translate: bool,
    mine: bool,
    bulk: bool,
    screenshot: str | None,
    seconds: float,
) -> None:
    for _ in range(demo_scroll):
        reader._scroll_tip(round(reader.osd[1] * 0.12))
    if demo_translate:
        reader._setup_secondary()
        reader.toggle_translation()
        time.sleep(0.3)
    if mine:
        (reader.bulk_mine if bulk else reader.mine_current)()
        time.sleep(0.5)
    if screenshot:
        time.sleep(0.4)
        r = ipc.command("screenshot-to-file", screenshot, "window")
        print("screenshot:", r, "->", screenshot)
        time.sleep(0.3)
    else:
        time.sleep(seconds)


def _execute_reader_session(
    reader,
    ipc,
    *,
    demo_word: str | None,
    screenshot: str | None,
    video: str | None,
    demo_scroll: int,
    demo_translate: bool,
    mine: bool,
    bulk: bool,
    seconds: float,
    translate_key: str,
) -> None:
    if demo_word or screenshot:
        time.sleep(0.8)
        text = _wait_for_subtitle_text(reader, ipc, video)
        print("sub-text:", repr(text))
        reader.set_subtitle(text)
        target = demo_word or "読む"
        idx = next((i for i, t in enumerate(reader.tokens) if target in t.surface), None)
        if idx is None:
            idx = next((i for i, t in enumerate(reader.tokens) if t.is_content), 0)
        print(f"demo hover → token[{idx}] = {reader.tokens[idx].surface!r}")
        reader.set_hover(idx)
        _run_demo_actions(
            reader,
            ipc,
            demo_scroll=demo_scroll,
            demo_translate=demo_translate,
            mine=mine,
            bulk=bulk,
            screenshot=screenshot,
            seconds=seconds,
        )
    else:
        print(
            f"reader running — hover words; '{translate_key}' toggles the EN translation; "
            "Ctrl+C or quit mpv to stop."
        )
        reader.run()


def run_impl(
    video: str | None,
    *,
    config: str | None,
    sub_file: str | None,
    slang: str,
    dicts: list[str] | None,
    translate_key: str,
    start: str,
    jimaku: bool,
    jimaku_key: str | None,
    jimaku_title: str | None,
    resync: bool,
    episode: int | None,
    width: int,
    height: int,
    fullscreen: bool,
    use_config: bool,
    demo_word: str | None,
    demo_translate: bool,
    demo_scroll: int,
    bulk: bool,
    screenshot: str | None,
    seconds: float,
    color: bool,
    known: str,
    anki_decks: str | None,
    freq: list[str] | None,
    pitch: list[str] | None,
    mine: bool,
    mine_deck: str,
    mine_model: str,
    mine_key: str,
    mine_all_key: str,
    mine_normalize_audio: bool,
    preview_key: str,
    no_audio_play: bool,
    mine_preview: bool,
    tip_height: float,
    pause_on_tooltip: bool,
    prefetch: bool,
    auto_translate: bool,
    hover_switch_delay: float,
    mpv_arg: list[str] | None = None,
) -> int:  # pragma: no cover — launches real mpv/ffmpeg (parse layer covered by test_cli)
    """Play a video with Japanese subs; hover a word → Yomitan-like dictionary tooltip in mpv."""
    from overlay.app.controller import Reader
    from overlay.app.reader_deps import begin_deps_build, warm_tokenizer

    cfg = load_config(config)
    setup_session_telemetry(
        cfg
    )  # BEFORE warm_tokenizer/begin_deps_build so their spans are captured

    # Fire this as early as possible — before mpv even launches — so fugashi's slow first-ever
    # tokenize() call (see warm_tokenizer's docstring) overlaps mpv's own launch/connect dead time
    # instead of landing on the critical path later.
    threading.Thread(target=warm_tokenizer, name="saitenka-tokenizer-warm", daemon=True).start()

    # A bare positional that isn't a real file (and isn't a URL) is almost always a mistyped or unknown
    # SUBCOMMAND landing on the default `run` shape — e.g. `saitenka install`. Don't hand it to
    # mpv as a filename (the cryptic "Failed to recognize file format"); show the commands instead.
    if video and "://" not in video and not Path(video).expanduser().exists():
        print(
            f"no such file: {video!r}\n"
            "If you meant a command, run `saitenka --help` — e.g. `setup`/`install` "
            "(configure options), `doctor` (health check), `install-plugin`, `import-settings`, "
            "`import-dictionaries`, `attach`.",
            file=sys.stderr,
        )
        return 2

    # resolve dict/freq/pitch lists: explicit CLI flags win, else fall back to the config file.
    # These are dictionary TITLES resolved against the consolidated DB — never built here.
    dict_titles = _resolve_names(dicts, cfg, "dicts")
    freq_titles = _resolve_names(freq, cfg, "freq")
    pitch_titles = _resolve_names(pitch, cfg, "pitch")
    known_cfg = json.loads(anki_decks) if anki_decks else cfg.get("known")

    if not (color or known_cfg or known or dict_titles or mine):
        print(
            "[hint] bare demo: no coloring, no monolingual dicts, no mining. Configure it once with\n"
            "       `saitenka setup`, or edit your config (see overlay.example.toml):\n"
            f"       {config_path()}\n"
            '       …or pass --dict … --freq … --pitch … --anki-decks \'{"Saitenka::Known":["Expression"]}\'\n'
            "       --mine  (see `saitenka run --help`)."
        )

    def _build_deps():
        return _build_run_deps(
            mine=mine,
            mine_deck=mine_deck,
            mine_model=mine_model,
            mine_key=mine_key,
            mine_all_key=mine_all_key,
            mine_normalize_audio=mine_normalize_audio,
            known_cfg=known_cfg,
            known=known,
            color=color,
            dict_titles=dict_titles,
            freq_titles=freq_titles,
            pitch_titles=pitch_titles,
        )

    # Hoist the dep build ahead of mpv launch (interactive path only; demo/screenshot build synchronously
    # below because they force-hover the instant mpv is up). The build touches no mpv IPC, so starting it
    # HERE overlaps mpv's launch/connect dead time (~0.2-0.9s measured) — the ~85ms build is fully hidden
    # and mpv's video is never delayed (separate process). load_deps_async consumes this once the reader
    # exists; without the hoist the build only started after connect, sitting idle through that whole window.
    deps_future = None if (demo_word or screenshot) else begin_deps_build(cfg, _build_deps)

    tmp, video_path, dur = _prepare_video(video, width, height, seconds)
    sub_path, en_sub_path, fetch_jimaku_in_background, enabled_providers = _resolve_subtitles(
        cfg,
        video,
        video_path,
        dur,
        tmp,
        sub_file=sub_file,
        jimaku=jimaku,
        jimaku_key=jimaku_key,
        jimaku_title=jimaku_title,
        episode=episode,
        resync=resync,
        slang=slang,
    )

    proc, ipc = _launch_mpv_and_connect(
        cfg,
        tmp,
        video_path,
        slang=slang,
        start=start,
        screenshot=screenshot,
        sub_path=sub_path,
        en_sub_path=en_sub_path,
        use_config=use_config,
        fullscreen=fullscreen,
        mpv_arg=mpv_arg,
    )
    if ipc is None:
        return 2

    from overlay.app.subtitle_modes import select_initial

    subtitle_startup = select_initial(ipc, slang)

    opts = _build_run_options(
        cfg,
        mine_key=mine_key,
        mine_all_key=mine_all_key,
        translate_key=translate_key,
        preview_key=preview_key,
        tip_height=tip_height,
        pause_on_tooltip=pause_on_tooltip,
        hover_switch_delay=hover_switch_delay,
        no_audio_play=no_audio_play,
        mine_preview=mine_preview,
        auto_translate=auto_translate,
        prefetch=prefetch,
    )

    # Demo/screenshot modes force-hover a word the instant mpv is up, so they need the dict set /
    # scorer / mining collaborators PRESENT synchronously — build them inline. The interactive path
    # builds them in the BACKGROUND (progressive startup): plain subs draw now, a spinner runs, and
    # coloring/tooltips/mining land in place once loaded.
    if demo_word or screenshot:
        scorer, anki, mine_conf, dict_set = _build_deps()
        reader = Reader(
            ipc, scorer=scorer, anki=anki, mine_cfg=mine_conf, dict_set=dict_set, options=opts
        )
    else:
        reader = Reader(ipc, options=opts)  # deps injected asynchronously below
        # index whatever track mpv ends up with (external/jimaku path, or an embedded track
        # extracted via ffmpeg) so Alt+←/→/↓ nav and prefetch lookahead both have upcoming lines
        build_sub_index_for_current_track(reader)
        reader.load_deps_async(
            cfg, prebuilt=deps_future
        )  # the build has been running since pre-launch

    reader.configure_subtitle_mode(subtitle_startup, slang=slang)
    _start_run_provider_fetch(
        reader,
        cfg,
        video_path,
        providers=(fetch_jimaku_in_background if subtitle_startup.tracks.jp_sid is None else ()),
        enabled_providers=enabled_providers,
        jimaku_title=jimaku_title,
        episode=episode,
        jimaku_key=jimaku_key,
        resync=resync,
    )

    try:
        _execute_reader_session(
            reader,
            ipc,
            demo_word=demo_word,
            screenshot=screenshot,
            video=video,
            demo_scroll=demo_scroll,
            demo_translate=demo_translate,
            mine=mine,
            bulk=bulk,
            seconds=seconds,
            translate_key=translate_key,
        )
    finally:
        try:
            reader.close()
            ipc.command("quit")
            ipc.close()
        except Exception:
            log.debug("reader/ipc shutdown cleanup failed", exc_info=True)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            from overlay.app.procutil import kill_process_tree

            kill_process_tree(proc)  # mpv didn't quit → kill it + any children (no orphans)
        else:
            _log_mpv_exit(proc.returncode)  # only meaningful for a self-exit, not our force-kill
    return 0
