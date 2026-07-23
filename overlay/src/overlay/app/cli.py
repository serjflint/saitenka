"""The real overlay entrypoint: a cyclopts CLI with ``run`` as the default command.

``examples/mpv_reader.py`` is now a thin wrapper around this module. HARD CONSTRAINT: every legacy
mpv_reader.py flag keeps its exact name and repeatable/negation behaviour (RUNNING.md is the
contract; ``tests/test_cli.py`` pins the inventory). The config file feeds defaults declaratively
via ``cyclopts.config.Toml`` (precedence: defaults < file < explicit CLI flags); the legacy-named
keys (``dicts``/``freq``/``pitch``/``known``/``[mine]``) are mapped explicitly, exactly as the old
argparse two-phase parse did.

Subcommands: ``doctor``, ``init``, ``import`` / ``import-settings`` / ``import-dictionaries`` (build the
consolidated dictionary DB), ``install-plugin`` / ``uninstall-plugin``, ``attach`` (joins a running mpv
and selects the JP sub track / fetches jimaku), and ``setup``.
"""

from __future__ import annotations

import logging
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Annotated

import cyclopts

from overlay import __version__
from overlay.app.config import TooltipOptions, config_path, load_config
from overlay.app.paths import cache_dir
from datetime import UTC

log = logging.getLogger(__name__)

DEMO_LINE = "門前の小僧習わぬ経を読む"
DEMO_LINE_EN = "A shop-boy at the temple gate recites sutras he was never taught."


def _ensure_free_threaded() -> None:
    """Adopt the free-threaded runtime: on a 3.14t build force the GIL OFF before fugashi's
    C extension loads (it hasn't declared FT-safety and would re-enable the GIL). Re-launch once so
    PYTHON_GIL=0 is set before the interpreter finishes starting. No-op on a standard build.

    Always re-launch via ``-m overlay.app.cli`` — NEVER via ``sys.argv[0]``: under ``python -m``,
    argv[0] is this file's path, and running it script-style would put ``src/overlay/app/`` first
    on sys.path, where our ``tokenize.py`` shadows the stdlib module and breaks the interpreter."""
    if sysconfig.get_config_var("Py_GIL_DISABLED") and os.environ.get("PYTHON_GIL") != "0":
        os.environ["PYTHON_GIL"] = "0"
        argv = [sys.executable, "-m", "overlay.app.cli", *sys.argv[1:]]
        if sys.platform == "win32":
            # os.execv on Windows does NOT truly replace the process — it duplicates execution and
            # corrupts the console (double output, and interactive prompts that can't take input).
            # Spawn a child that shares our console, wait, and exit with its status instead.
            try:
                sys.exit(subprocess.run(argv).returncode)
            except KeyboardInterrupt:
                # Ctrl+C on the shared console reaches BOTH processes: the child cleans up and exits
                # on its own SIGINT; the parent must not dump a KeyboardInterrupt traceback from
                # subprocess.wait(). Exit quietly with the conventional 130 (128 + SIGINT).
                sys.exit(130)
        os.execv(sys.executable, argv)


def _resolve_names(flag_vals: list[str] | None, cfg: dict, key: str) -> list[str]:
    """Flag values win over the config file. Values are dictionary **titles** resolved against the
    consolidated DB (imported once) — not paths, so no ~/$VAR expansion is needed."""
    return list(flag_vals or []) or list(cfg.get(key) or [])


def _print_legacy_note() -> None:  # pragma: no cover — cosmetic, filesystem-dependent
    """After a successful import, point out pre-consolidation files that are now unused (the old per-zip
    caches and the copied dictionary zips) — informational only, nothing is deleted."""
    from overlay.app.paths import legacy_dict_artifacts

    arts = legacy_dict_artifacts()
    if not arts:
        return
    total = sum(b for _, _, b in arts)
    print(
        f"\nnote: {total / 1e6:.0f} MB of pre-consolidation files are now unused and safe to delete "
        "(the DB no longer needs them):"
    )
    for d, n, b in arts:
        print(f"  {d}  ({n} files, {b / 1e6:.0f} MB)")


def jimaku_should_fetch(
    explicit_flag: bool, cfg_fetch: bool, video: str | None, slang: str = "ja,jpn,jp", probe=None
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


def _argv_config_override(argv: list[str]) -> str | None:
    """Pre-scan argv for ``--config PATH`` (phase 1 of the legacy two-phase parse)."""
    for i, tok in enumerate(argv):
        if tok == "--config" and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--config="):
            return tok.split("=", 1)[1]
    return None


# Loaded at import so the [mine] table can seed signature defaults, exactly like the legacy
# two-phase argparse did. (Module reload picks up $SAITENKA_CONFIG changes — tests rely on it.)
_cfg = load_config()
_mine_cfg = _cfg.get("mine", {}) if isinstance(_cfg.get("mine"), dict) else {}

app = cyclopts.App(
    name="saitenka-overlay",
    help="Saitenka in-mpv overlay: JP subs with FSRS coloring, hover → multi-dict tooltip, mining.",
    # Pin the version explicitly — cyclopts otherwise resolves it from the `overlay` import package's
    # metadata, which has no distribution (the dist is `saitenka-overlay`), so `--version` printed 0.0.0.
    version=__version__,
    config=cyclopts.config.Toml(
        config_path(), must_exist=False, use_commands_as_keys=False, allow_unknown=True
    ),
)


@app.command(name="run")
def run(
    video: str | None = None,
    *,
    config: Annotated[
        str | None,
        cyclopts.Parameter(help="settings TOML (default: platform config dir, see `doctor`)"),
    ] = None,
    sub_file: str | None = None,
    slang: Annotated[
        str, cyclopts.Parameter(help="primary (JP) sub languages, priority order")
    ] = "ja,jpn,jp",
    dicts: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            name="--dict",
            negative=(),
            help="imported dictionary TITLE (repeatable; ordered — first = top of the tooltip)",
        ),
    ] = None,
    translate_key: Annotated[
        str, cyclopts.Parameter(help="mpv key to toggle the EN translation")
    ] = "t",
    start: Annotated[str, cyclopts.Parameter(help="mpv --start (seconds or hh:mm:ss)")] = "1",
    jimaku: Annotated[
        bool, cyclopts.Parameter(negative=(), help="fetch JP subs from jimaku.cc")
    ] = False,
    jimaku_key: Annotated[
        str | None, cyclopts.Parameter(help="jimaku.cc API key (else $JIMAKU_API_KEY)")
    ] = None,
    jimaku_title: Annotated[
        str | None, cyclopts.Parameter(help="override the title parsed from the filename")
    ] = None,
    resync: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-resync",
            help="auto-resync jimaku-sourced subtitles via alass/ffsubsync (default: on)",
        ),
    ] = True,
    episode: Annotated[
        int | None, cyclopts.Parameter(help="override the episode parsed from the filename")
    ] = None,
    width: Annotated[int, cyclopts.Parameter(help="test-clip width (default 1080p)")] = 1920,
    height: int = 1080,
    fullscreen: Annotated[bool, cyclopts.Parameter(negative=())] = False,
    use_config: Annotated[bool, cyclopts.Parameter(negative=())] = False,
    demo_word: Annotated[
        str | None, cyclopts.Parameter(help="force-hover the first token containing this text")
    ] = None,
    demo_translate: Annotated[
        bool, cyclopts.Parameter(negative=(), help="reveal the EN translation (demo)")
    ] = False,
    demo_scroll: Annotated[int, cyclopts.Parameter(help="scroll the tooltip N steps (demo)")] = 0,
    bulk: Annotated[
        bool, cyclopts.Parameter(negative=(), help="in demo, bulk-mine the cue instead of one word")
    ] = False,
    screenshot: Annotated[
        str | None, cyclopts.Parameter(help="capture the composited window to this PNG, then quit")
    ] = None,
    seconds: float = 60.0,
    color: Annotated[
        bool, cyclopts.Parameter(negative=(), help="enable SubMiner-style word coloring")
    ] = False,
    known: Annotated[
        str, cyclopts.Parameter(help="comma-separated known words (lemmas/readings)")
    ] = "",
    anki_decks: Annotated[
        str | None,
        cyclopts.Parameter(help='JSON {"Deck": ["Field"]} to build known-set via AnkiConnect'),
    ] = None,
    freq: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(),
            help="imported frequency-dict TITLE (repeatable; green pills + coloring bands)",
        ),
    ] = None,
    pitch: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(), help="imported pitch-accent-dict TITLE (repeatable; purple pills)"
        ),
    ] = None,
    mine: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine",
            help="one-key mining to Anki (default: on when [mine] is configured; --no-mine to disable)",
        ),
    ] = bool(_mine_cfg.get("enabled", bool(_mine_cfg))),
    mine_deck: str = _mine_cfg.get("deck", "Saitenka::Mining"),
    mine_model: str = _mine_cfg.get("model", "Lapis"),
    mine_key: Annotated[
        str, cyclopts.Parameter(help="mpv key that mines the hovered word")
    ] = _mine_cfg.get("key", "Ctrl+m"),
    mine_all_key: Annotated[
        str, cyclopts.Parameter(help="mpv key that bulk-mines the cue")
    ] = _mine_cfg.get("all_key", "Shift+m"),
    preview_key: Annotated[
        str, cyclopts.Parameter(help="mpv key to replay the last card preview + audio")
    ] = _mine_cfg.get("preview_key", "p"),
    no_audio_play: Annotated[
        bool, cyclopts.Parameter(negative=(), help="don't auto-play the mined clip")
    ] = False,
    tip_height: Annotated[
        float,
        cyclopts.Parameter(
            help=f"max BASE tooltip height as a fraction of the video height "
            f"(default {TooltipOptions().tip_max_frac})"
        ),
        # The default lives once, on TooltipOptions.tip_max_frac. cyclopts still layers
        # defaults < config < CLI (token-based), so sourcing the floor here changes nothing but DRY.
    ] = TooltipOptions().tip_max_frac,
    dict_tabs: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-dict-tabs",
            help="draw the sticky per-dictionary tab strip on the tooltip (default: off)",
        ),
    ] = False,
    pause_on_tooltip: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-pause-on-tooltip",  # on by default now → give an explicit off switch
            help="auto-pause playback while a tooltip is shown (resumes when it hides)",
        ),
    ] = True,
    prefetch: Annotated[
        bool,
        cyclopts.Parameter(
            name=(),  # only the negative form exists, exactly like the legacy --no-prefetch
            negative="--no-prefetch",
            help="disable background prefetch of the paused line's tooltips",
        ),
    ] = True,
    auto_translate: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="auto-reveal the EN translation while a tooltip is shown (else press the translate "
            "key). Anti-crutch: the EN only appears when you're looking a word up",
        ),
    ] = False,
    hover_switch_delay: Annotated[
        float,
        cyclopts.Parameter(
            help="seconds the cursor must rest on a NEW word before the tooltip switches to it "
            "(0 = instant)"
        ),
    ] = 0.15,
) -> int:  # pragma: no cover — launches real mpv/ffmpeg (parse layer covered by test_cli)
    """Play a video with Japanese subs; hover a word → Yomitan-like dictionary tooltip in mpv."""
    from overlay.app.controller import Reader
    from overlay.mpvio.ipc import MpvIPC

    # A bare positional that isn't a real file (and isn't a URL) is almost always a mistyped or unknown
    # SUBCOMMAND landing on the default `run` shape — e.g. `saitenka-overlay install`. Don't hand it to
    # mpv as a filename (the cryptic "Failed to recognize file format"); show the commands instead.
    if video and "://" not in video and not Path(video).expanduser().exists():
        print(
            f"no such file: {video!r}\n"
            "If you meant a command, run `saitenka-overlay --help` — e.g. `setup`/`install` "
            "(configure options), `doctor` (health check), `install-plugin`, `import-settings`, "
            "`import-dictionaries`, `attach`.",
            file=sys.stderr,
        )
        return 2

    cfg = load_config(config)

    # resolve dict/freq/pitch lists: explicit CLI flags win, else fall back to the config file.
    # These are dictionary TITLES resolved against the consolidated DB — never built here.
    dict_titles = _resolve_names(dicts, cfg, "dicts")
    freq_titles = _resolve_names(freq, cfg, "freq")
    pitch_titles = _resolve_names(pitch, cfg, "pitch")
    known_cfg = json.loads(anki_decks) if anki_decks else cfg.get("known")

    if not (color or known_cfg or known or dict_titles or mine):
        print(
            "[hint] bare demo: no coloring, no monolingual dicts, no mining. Configure it once with\n"
            "       `saitenka-overlay setup`, or edit your config (see overlay.example.toml):\n"
            f"       {config_path()}\n"
            '       …or pass --dict … --freq … --pitch … --anki-decks \'{"Saitenka::Known":["Expression"]}\'\n'
            "       --mine  (see RUNNING.md §3)."
        )

    # Building the coloring/dict/mining collaborators is the slow part (the first-run dictionary
    # cache build is 25–66s per dict). Rather than block the mpv window on it, `run` defers this to a
    # BACKGROUND thread (see reader.load_deps_async below) so plain subtitles draw immediately and
    # coloring/tooltips/mining light up in place once loaded — exactly like `attach`. The closure
    # captures the CLI-flag inputs (`--dict/--freq/--anki-decks/--mine` …) that config-only
    # build_reader_deps can't see. It must NOT touch the mpv IPC (it runs off the main thread); its
    # prints go to the terminal, the in-mpv spinner covers the on-screen feedback.
    def _build_deps():
        # Anki-backed features — mining, and known-word coloring from a deck — need Anki running.
        # Start it for the user (like `attach` does) instead of crashing on a refused connection;
        # warn and degrade (coloring → freq+JLPT, mining unavailable) if it can't be reached.
        if mine or known_cfg:
            from overlay.app.anki import ensure_anki_running

            if not ensure_anki_running():
                print(
                    "note: Anki/AnkiConnect not reachable — start Anki (with the AnkiConnect "
                    "add-on). Coloring falls back to freq+JLPT; mining is unavailable until it's up.",
                    file=sys.stderr,
                )

        anki = mine_conf = None
        if mine:
            from overlay.app.anki import Anki, MineConfig

            anki = Anki()
            mine_conf = MineConfig(deck=mine_deck, model=mine_model)
            print(
                f"mining on — {mine_key} mine · {mine_all_key or 'Shift+m'} mine-all "
                f"→ {mine_deck} ({mine_model})"
            )
            log.info("mining enabled: deck=%r model=%r key=%r", mine_deck, mine_model, mine_key)
        else:
            log.info("mining disabled (no [mine] config / --no-mine)")

        # Resolve dict/freq/pitch TITLES against the consolidated DB (imported once by `import`);
        # a title with no imported dictionary is warned and skipped — nothing is built here.
        from overlay.app.dictdb import DictionaryDb

        db = DictionaryDb.open()
        dict_set = None
        freq_rows: list = []
        if dict_titles or freq_titles or pitch_titles:
            from overlay.app.dictionary import _MISSING_HINT, DictionarySet

            d_rows, dmiss = db.resolve(dict_titles)
            freq_rows, fmiss = db.resolve(freq_titles)
            p_rows, pmiss = db.resolve(pitch_titles)
            for kind, miss in (("dict", dmiss), ("freq", fmiss), ("pitch", pmiss)):
                if miss:
                    print(
                        f"{kind}(s) not imported, skipped: {', '.join(repr(m) for m in miss)}. "
                        f"{_MISSING_HINT}",
                        file=sys.stderr,
                    )
            if d_rows or freq_rows or p_rows:
                dict_set = DictionarySet.from_rows(db, d_rows, freq_rows, p_rows)
                print("dictionaries:", [d.title for d in dict_set.dicts])
                if dict_set.freqs:
                    print("frequency:", [f.title for f in dict_set.freqs])
                if dict_set.pitches:
                    print("pitch:", [p.title for p in dict_set.pitches])
                log.info(
                    "dictionaries loaded: %d defn, %d freq, %d pitch",
                    len(dict_set.dicts),
                    len(dict_set.freqs),
                    len(dict_set.pitches),
                )

        scorer = None
        if color or known or known_cfg or freq_titles:
            from overlay.app.scoring import Scorer
            from overlay.app.wordlists import FreqDict, JlptDict, KnownWords

            if known_cfg:
                try:
                    kw = KnownWords.from_ankiconnect(known_cfg)
                except Exception as e:  # Anki still closed / AnkiConnect down — don't crash the run
                    print(
                        f"known-word load from Anki failed ({e}) — coloring by freq+JLPT only",
                        file=sys.stderr,
                    )
                    kw = KnownWords.from_set([w for w in known.split(",") if w])
            else:
                kw = KnownWords.from_set([w for w in known.split(",") if w])
            if not freq_rows:  # scorer may be on without a dict set (coloring-only run)
                freq_rows, _ = db.resolve(freq_titles)
            fd = FreqDict.from_db(db, freq_rows[0]) if freq_rows else None
            scorer = Scorer(known=kw, freq=fd, jlpt=JlptDict.load(db))
            print(f"coloring on — known:{len(kw.words)} freq:{bool(fd)} jlpt:on")

        return scorer, anki, mine_conf, dict_set

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-reader-"))
    dur = max(8, int(seconds))
    video_path = Path(video).expanduser() if video else tmp / "clip.mp4"
    if not video:
        print(f"no video — generating a {width}x{height} test clip…")
        _make_clip(video_path, dur, width, height)

    # subtitle source: explicit file > jimaku fetch > embedded track (--slang) > generated demo line.
    # jimaku fires on --jimaku OR when the config enables it (`[jimaku].fetch = true`); the config path
    # only fetches when the file has NO embedded JP track, so it doesn't override good embedded subs.
    _jm = cfg.get("jimaku")
    jimaku_cfg = _jm if isinstance(_jm, dict) else {}
    jimaku_on = jimaku_should_fetch(
        jimaku, bool(jimaku_cfg.get("fetch")), str(video_path) if video else None, slang
    )
    log.info(
        "jimaku fetch: %s (flag=%s cfg_fetch=%s)", jimaku_on, jimaku, bool(jimaku_cfg.get("fetch"))
    )
    sub_path = en_sub_path = None
    if sub_file:
        sub_path = Path(sub_file).expanduser()
    elif jimaku_on:
        from overlay.app.jimaku import (
            JimakuClient,
            JimakuError,
            cached_subs,
            parse_filename,
            store_subs,
        )

        title, ep = parse_filename(video_path)
        title = jimaku_title or title
        ep = episode if episode is not None else ep
        hit = cached_subs(video_path, title, ep) if video_path.exists() else None
        if hit:
            print("jimaku: using cached subs", hit.name)
            sub_path = hit
            log.info("jimaku cache hit: %s", hit)
        else:
            print(f"jimaku: fetching subs for {title!r} ep {ep}…")
            try:
                sub_path = JimakuClient(jimaku_key or jimaku_cfg.get("key")).fetch(title, ep, tmp)
                print("jimaku: got", sub_path.name)
                if resync and video_path.exists():
                    from overlay.app.resync import maybe_resync

                    print("jimaku: resyncing…")
                    sub_path = maybe_resync(video_path, sub_path, enabled=True)
                    print("jimaku: resync →", sub_path.name)
                if video_path.exists():  # cache the finished (synced) sub for the next rewatch
                    sub_path = store_subs(video_path, title, ep, sub_path)
            except JimakuError as e:
                print("jimaku failed:", e, "— falling back to embedded/default", file=sys.stderr)
    elif not video:
        sub_path = tmp / "line.srt"
        _make_srt(sub_path, dur, DEMO_LINE)
        en_sub_path = tmp / "line.en.srt"  # secondary EN track → test the `t` translation reveal
        _make_srt(en_sub_path, dur, DEMO_LINE_EN)

    from overlay.mpvio.discover import find_mpv
    from overlay.mpvio.ipc import default_ipc_path

    mpv_bin = find_mpv(cfg.get("mpv_path"))
    if not mpv_bin:
        print(
            "mpv not found — install it (Windows: `winget install shinchiro.mpv`; macOS: "
            "`brew install mpv`), or set `mpv_path` in overlay.toml. Run `saitenka-overlay doctor`.",
            file=sys.stderr,
        )
        return 2
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
        return 2

    from overlay.app.config import (
        KeyOptions,
        MiningOptions,
        PerfOptions,
        ReaderOptions,
        TooltipOptions,
        TranslationOptions,
    )

    _tt, _mo, _po = TooltipOptions(), MiningOptions(), PerfOptions()
    opts = ReaderOptions(
        keys=KeyOptions(
            mine_key=mine_key,
            mine_all_key=mine_all_key,
            translate_key=translate_key,
            preview_key=preview_key,
            sub_prev_key=cfg.get("sub_prev_key", "Alt+LEFT"),
            sub_next_key=cfg.get("sub_next_key", "Alt+RIGHT"),
            sub_replay_key=cfg.get("sub_replay_key", "Alt+DOWN"),
        ),
        tooltip=TooltipOptions(
            tip_max_frac=tip_height,
            nested_max_frac=cfg.get("nested_max_frac", _tt.nested_max_frac),
            pause_on_tooltip=pause_on_tooltip,
            hover_switch_delay=hover_switch_delay,
            hide_delay=cfg.get("hide_delay", _tt.hide_delay),
            flash_secs=cfg.get("flash_secs", _tt.flash_secs),
            # off by default; on if EITHER --dict-tabs is passed or the config enables it
            show_dict_tabs=dict_tabs or bool(cfg.get("show_dict_tabs", False)),
            panel_cache_max=cfg.get("panel_cache_max", _tt.panel_cache_max),
        ),
        mining=MiningOptions(
            play_audio=not no_audio_play,
            max_bulk=cfg.get("max_bulk", _mo.max_bulk),
            anki_ok_ttl=cfg.get("anki_ok_ttl", _mo.anki_ok_ttl),
            anki_ping_timeout=cfg.get("anki_ping_timeout", _mo.anki_ping_timeout),
        ),
        translation=TranslationOptions(auto_translate=auto_translate),
        perf=PerfOptions(
            poll_interval=cfg.get("poll_interval", _po.poll_interval),
            prefetch_workers=cfg.get("prefetch_workers", _po.prefetch_workers),
        ),
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
        if sub_path:  # index the external sub so Alt+←/→/↓ can render the target line instantly
            reader.load_sub_index(sub_path)
        reader.load_deps_async(cfg, build=_build_deps)
    try:
        if demo_word or screenshot:
            time.sleep(0.8)
            reader.refresh_osd()
            text = reader._get("sub-text") or ""
            if not text and video:  # real file: hop to the next subtitle cue
                for _ in range(80):
                    ipc.command("sub-seek", 1)
                    time.sleep(0.12)
                    text = reader._get("sub-text") or ""
                    if text:
                        break
            text = text or DEMO_LINE
            print("sub-text:", repr(text))
            reader.set_subtitle(text)
            target = demo_word or "読む"
            idx = next((i for i, t in enumerate(reader.tokens) if target in t.surface), None)
            if idx is None:
                idx = next((i for i, t in enumerate(reader.tokens) if t.is_content), 0)
            print(f"demo hover → token[{idx}] = {reader.tokens[idx].surface!r}")
            reader.set_hover(idx)
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
        else:
            print(
                f"reader running — hover words; '{translate_key}' toggles the EN translation; "
                "Ctrl+C or quit mpv to stop."
            )
            reader.run()
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
    return 0


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


# --- setup / maintenance subcommands ---------------------------------------------------------------


@app.command
def doctor(
    *,
    json_out: Annotated[
        bool, cyclopts.Parameter(name="--json", negative=(), help="emit the report as JSON")
    ] = False,
    summary: Annotated[
        bool,
        cyclopts.Parameter(
            name=("--summary", "--quiet"),
            negative=(),
            help="collapse passing checks to a count; show only warnings/failures in full",
        ),
    ] = False,
    mine_deck: str = _mine_cfg.get("deck", "Saitenka::Mining"),
    mine_model: str = _mine_cfg.get("model", "Lapis"),
) -> int:  # pragma: no cover — thin CLI wrapper; run_checks/print_report are unit-tested
    """Check the environment: mpv/ffmpeg, config, dict cache, fonts, AnkiConnect."""
    from overlay.app.doctor import print_report, run_checks

    report = run_checks(deck=mine_deck, model=mine_model)
    if json_out:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        print_report(report, summary=summary)
    return report.exit_code


@app.command(
    show=False
)  # low-level primitive — end users run `setup` (which calls this); hidden from help
def init() -> int:  # pragma: no cover — interactive wizard, exercised live
    """Write a starter config (the config-file primitive `setup` builds on). Prefer `setup`/`install`."""
    from overlay.app.init_wizard import run_init

    return run_init()


@app.command(name="import")
def import_dicts(
    paths: Annotated[
        list[str],
        cyclopts.Parameter(help="Yomitan dictionary .zip files and/or folders of them"),
    ],
    *,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="write the config without prompting")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; gather_yomitan_zips/import_zips are unit-tested
    """Import Yomitan dictionary .zip files into the consolidated database (built once) and register
    them in the config by title.

    Accepts individual ``.zip`` files and/or directories to scan for them. Each is classified by content
    (definition / frequency / pitch) and imported into ``data_dir()/dictionaries.sqlite``. The source
    zips are read **in place** — no copy is kept — so you can delete or move them afterwards."""
    from datetime import datetime

    from overlay.app.init_wizard import _ask, dumps_toml, write_config
    from overlay.app.progress import BuildBar
    from overlay.app.yomitan_import import gather_yomitan_zips, import_zips

    zips = gather_yomitan_zips(list(paths))
    if not zips:
        print(
            "no Yomitan dictionaries found (looked for .zip files carrying an index.json)",
            file=sys.stderr,
        )
        return 1
    print(f"importing {len(zips)} dictionary/ies into the database…")
    bar = BuildBar()
    try:
        cfg = import_zips(zips, imported_at=datetime.now(UTC).isoformat(), progress=bar.update)
    finally:
        bar.close()
    for kind in ("dicts", "freq", "pitch"):
        if cfg.get(kind):
            print(f"  {kind}: {cfg[kind]}")
    merged = {**load_config(), **cfg}  # overlay the imported titles onto the existing config
    print("\nProposed config:")
    print(dumps_toml(merged))
    backup = write_config(merged, confirm=(lambda _p: True) if yes else _ask)
    if backup:
        print(f"backed up existing config → {backup}")
    _print_legacy_note()
    return 0


@app.command(name="set-jimaku-key")
def set_jimaku_key(
    key: Annotated[
        str | None, cyclopts.Parameter(help="the key (omit to be prompted with hidden input)")
    ] = None,
) -> int:  # pragma: no cover — interactive/secret I/O; keychain_set is unit-tested
    """Store your jimaku.cc API key where a plugin-mode (GUI-launched) mpv can read it.

    macOS: the login Keychain. Windows/Linux (no Keychain): ``[jimaku].key`` in overlay.toml. Either
    beats a shell env var, which a GUI-launched mpv can't see. Get a free key at https://jimaku.cc/profile
    (API docs: https://jimaku.cc/api/docs).

    Windows paste tip: the hidden prompt does NOT accept Ctrl+V (it captures one control char), so a
    pasted key can silently truncate to a single character. Right-click to paste at the prompt, or pass
    the key as an argument on the normal command line where Ctrl+V works: ``set-jimaku-key <key>``.
    """
    import getpass

    from overlay.app.config import config_path
    from overlay.app.init_wizard import store_jimaku_key
    from overlay.app.jimaku import key_paste_warning, prompt_for_key

    if (
        key is None
    ):  # interactive: hidden prompt with a truncated-paste guard (the Windows Ctrl+V trap)
        k = prompt_for_key(getpass.getpass)
    else:  # key passed as an argument (paste-safe on the normal line) — still sanity-check its length
        k = key.strip()
        warn = key_paste_warning(k)
        if warn:
            print(warn, file=sys.stderr)
    if not k:
        print("no key entered", file=sys.stderr)
        return 2
    method, backup = store_jimaku_key(k)
    if method == "keyring":
        print("stored in the OS secret store (Keychain / Credential Locker / Secret Service)")
    else:
        print(f"stored in {config_path()} as [jimaku].key (plaintext — keep the file private)")
        if backup:
            print(f"backed up existing config → {backup}")
    return 0


@app.command(name="jimaku-check")
def jimaku_check(
    query: Annotated[str, cyclopts.Parameter(help="anime title to test-search")] = "Spy x Family",
) -> int:  # pragma: no cover — thin CLI wrapper; JimakuClient is tested
    """Diagnose jimaku without launching a video: resolve the key and run a test search, printing the
    exact outcome (key found? 200 OK / 401 bad key / 400 + server message / network error)."""
    from overlay.app.jimaku import JimakuClient, JimakuError, resolve_jimaku_key

    key, src = resolve_jimaku_key()
    if not key:
        print("jimaku key: NOT configured — run `saitenka-overlay set-jimaku-key`", file=sys.stderr)
        return 1
    print(f"jimaku key: found (from {src}), {len(key)} chars")
    try:
        entries = JimakuClient().search(query)
        head = f" — first: {entries[0].get('name')!r}" if entries else ""
        print(f"search {query!r}: OK — {len(entries)} entrie(s){head}")
        return 0
    except JimakuError as e:
        print(f"search {query!r}: {e}", file=sys.stderr)
        return 1


@app.command(name="import-settings", alias="import-yomitan")
def import_settings(
    settings: str | None = None,
    *,
    scan_dir: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(),
            help="dir holding your Yomitan dictionary .zip files (repeatable; opt-in — no personal "
            "folder is scanned unless you name it). Titles are matched against these dirs.",
        ),
    ] = None,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="write the config without prompting")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; parse/map/match are unit-tested
    """Apply a Yomitan SETTINGS export (dictionary order + options) to your overlay config.

    Reads the small Yomitan → Settings → Backup → Export Settings file and matches its dictionary
    titles against the ``.zip`` files under ``--scan-dir``. For a full Yomitan DATABASE backup (the
    multi-GB export), use ``import-dictionaries`` instead — it unpacks that into ``.zip`` dicts.
    (Alias: ``import-settings``.)
    """
    from overlay.app.init_wizard import _ask
    from overlay.app.yomitan_import import YomitanImportError, run_import

    confirm = (lambda _p: True) if yes else _ask
    try:
        return run_import(settings, scan_dir, confirm)
    except YomitanImportError as e:
        print(f"import failed: {e}", file=sys.stderr)
        return 1


@app.command(name="import-dictionaries")
def import_dictionaries(
    export: str,
    *,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="write the config without prompting")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; streaming import + converters are unit-tested
    """Import a Yomitan DATABASE backup (the multi-GB dexie JSON export) directly into the consolidated
    database. Streamed — never full-loaded. The per-dictionary zips are reconstructed into a TEMP dir,
    imported, then discarded (no persistent zip copies are kept).

    This is for when you DON'T have the dictionary .zip files. If you already have them, use
    ``import`` / ``import-settings`` (faster, no unpacking)."""
    import tempfile
    from datetime import datetime

    from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

    from overlay.app.init_wizard import _ask, dumps_toml, write_config
    from overlay.app.progress import BuildBar
    from overlay.app.yomitan_db_import import YomitanDbImportError, import_database, read_header
    from overlay.app.yomitan_import import import_zips

    try:
        _, total = read_header(export)
    except YomitanDbImportError as e:
        print(f"import failed: {e}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="saitenka-dbimport-") as tmp:
        print(f"streaming {total:,} rows from {export} → temp staging → database")
        paths: list[Path] = []
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed:,}/{task.total:,} rows"),
            TimeRemainingColumn(),
        ) as prog:
            task = prog.add_task("unpacking", total=total or None)
            last = 0

            def _cb(done: int, tot: int) -> None:
                nonlocal last
                if done - last >= 20_000 or done == tot:  # throttle: millions of rows
                    prog.update(task, completed=done)
                    last = done

            try:
                paths = import_database(export, Path(tmp), progress=_cb)
            except YomitanDbImportError as e:
                print(f"import failed: {e}", file=sys.stderr)
                return 1
            prog.update(task, completed=total)

        if not paths:
            print("no dictionaries found in the export", file=sys.stderr)
            return 1

        print(f"\nbuilding {len(paths)} dictionaries into the database…")
        bar = BuildBar()
        try:
            cfg = import_zips(
                [str(p) for p in paths],
                imported_at=datetime.now(UTC).isoformat(),
                progress=bar.update,
            )
        finally:
            bar.close()

    for kind in ("dicts", "freq", "pitch"):
        if cfg.get(kind):
            print(f"  {kind}: {cfg[kind]}")
    merged = {**load_config(), **cfg}  # overlay the imported titles onto the existing config
    print("\nProposed config:")
    print(dumps_toml(merged))
    backup = write_config(merged, confirm=(lambda _p: True) if yes else _ask)
    if backup:
        print(f"backed up existing config → {backup}")
    return 0


@app.command(name="install-plugin")
def install_plugin() -> int:  # pragma: no cover — thin CLI wrapper; plugin ops are unit-tested
    """Install the saitenka.lua mpv user-script (plugin mode)."""
    from overlay.app.plugin import install_plugin as do_install

    dest = do_install()
    print(f"installed {dest}")
    print(
        "mpv will now spawn `saitenka-overlay attach <socket>` on file-loaded, from any launcher."
    )
    return 0


@app.command(name="uninstall-plugin")
def uninstall_plugin() -> int:  # pragma: no cover — thin CLI wrapper; plugin ops are unit-tested
    """Remove the saitenka.lua mpv user-script (backs it up first)."""
    from overlay.app.plugin import uninstall_plugin as do_uninstall

    backup = do_uninstall()
    if backup is None:
        print("saitenka.lua was not installed — nothing to do")
    else:
        print(f"removed saitenka.lua (backup at {backup})")
    return 0


@app.command
def report(
    *,
    out: Annotated[
        str | None,
        cyclopts.Parameter(
            help="directory to write the zip into (default: the data dir's reports/)"
        ),
    ] = None,
    no_log: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="exclude the overlay log (may contain video filenames / mined sentences)",
        ),
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; collect/redact/bundle are unit-tested
    """Bundle diagnostics (doctor + versions + config + mpv.conf + plugin lua + log) into a single
    timestamped zip for bug reports. Local-only, never uploaded; secrets are redacted."""
    from overlay.app.report import build_report_bundle

    dest = build_report_bundle(out, include_log=not no_log)
    print(f"wrote {dest}")
    print(
        "Review it before sharing — API keys were removed, but it includes your config, mpv.conf, and"
        + (
            " the overlay log (video filenames / mined sentences may appear)."
            if not no_log
            else " no log."
        )
    )
    return 0


@app.command(alias="install")
def setup(
    *,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="answer yes to every prompt")
    ] = False,
    dry_run: Annotated[
        bool, cyclopts.Parameter(negative=(), help="show what would happen, change nothing")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; the wizard steps are unit-tested
    """One-command setup (alias: ``install``): inventory → install mpv+ffmpeg → doctor → init →
    import → plugin. Re-run any time to reconfigure — it's resumable and confirm-first."""
    from overlay.app.setup_wizard import run_setup

    return run_setup(yes=yes, dry_run=dry_run)


@app.command
def attach(
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
) -> (
    int
):  # pragma: no cover — connects to a live mpv; the reader loop is covered by controller tests
    """Attach to an already-running mpv's IPC socket instead of launching mpv.

    mpv accepts multiple concurrent IPC clients, so we JOIN a socket shared with
    mpv_websocket/animecards rather than take it over. On attach we actively select the Japanese
    subtitle track (the user's mpv may prefer English), fetching from jimaku when asked.
    """
    from overlay.app.config import (
        KeyOptions,
        MiningOptions,
        PerfOptions,
        ReaderOptions,
        TooltipOptions,
        TranslationOptions,
    )
    from overlay.app.controller import Reader
    from overlay.mpvio.ipc import MpvIPC

    cfg = load_config(config)
    sock = socket or cfg.get("mpv_socket")
    if not sock:
        print(
            "no socket given — pass one (e.g. --attach /tmp/mpv-socket) or set mpv_socket in the "
            "config, or add `input-ipc-server=<path>` to mpv.conf",
            file=sys.stderr,
        )
        return 2

    # Step aside if SubMiner is running — it injects its own mpv overlay, and two overlays over one
    # video flicker / stick on "overlay loading". Quit SubMiner (or uninstall its plugin) to use this.
    from overlay.app.conflicts import subminer_running

    if subminer_running():
        msg = "SubMiner is running — skipping the saitenka overlay to avoid a double overlay. Quit SubMiner to use saitenka."
        log.warning("attach: %s", msg)
        print(msg, file=sys.stderr, flush=True)
        return 0

    try:
        ipc = MpvIPC(sock).connect(timeout=15)
    except TimeoutError as e:
        print(f"could not attach to mpv IPC at {sock}: {e}", file=sys.stderr)
        return 2

    from overlay.app.subselect import ensure_jp_subs

    # [jimaku] config table feeds attach defaults so plugin mode (which spawns a bare `attach`) can
    # fetch subs without CLI flags. An explicit --jimaku / --jimaku-key still wins.
    _jm = cfg.get("jimaku")
    jm = _jm if isinstance(_jm, dict) else {}
    jimaku_force = jimaku_force or bool(jm.get("force", False))
    jimaku = jimaku or jimaku_force or bool(jm.get("enabled", False))  # force implies fetch
    jimaku_key = jimaku_key or jm.get("key")
    resync = resync and bool(jm.get("resync", True))

    try:
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
    _tt, _mo, _po = TooltipOptions(), MiningOptions(), PerfOptions()

    opts = ReaderOptions(
        keys=KeyOptions(
            mine_key=mc.get("key", "Ctrl+m"),
            mine_all_key=mc.get("all_key", "Shift+m"),
            preview_key=mc.get("preview_key", "p"),
            translate_key=cfg.get("translate_key", "t"),
            sub_prev_key=cfg.get("sub_prev_key", "Alt+LEFT"),
            sub_next_key=cfg.get("sub_next_key", "Alt+RIGHT"),
            sub_replay_key=cfg.get("sub_replay_key", "Alt+DOWN"),
        ),
        tooltip=TooltipOptions(
            tip_max_frac=cfg.get("tip_height", _tt.tip_max_frac),
            nested_max_frac=cfg.get("nested_max_frac", _tt.nested_max_frac),
            show_dict_tabs=bool(cfg.get("show_dict_tabs", False)),
            hide_delay=cfg.get("hide_delay", _tt.hide_delay),
            flash_secs=cfg.get("flash_secs", _tt.flash_secs),
            panel_cache_max=cfg.get("panel_cache_max", _tt.panel_cache_max),
        ),
        mining=MiningOptions(
            play_audio=not bool(cfg.get("no_audio_play", False)),
            max_bulk=cfg.get("max_bulk", _mo.max_bulk),
            anki_ok_ttl=cfg.get("anki_ok_ttl", _mo.anki_ok_ttl),
            anki_ping_timeout=cfg.get("anki_ping_timeout", _mo.anki_ping_timeout),
        ),
        translation=TranslationOptions(auto_translate=bool(cfg.get("auto_translate", False))),
        perf=PerfOptions(
            poll_interval=cfg.get("poll_interval", _po.poll_interval),
            prefetch_workers=cfg.get("prefetch_workers", _po.prefetch_workers),
        ),
        overlay_id_base=int(cfg.get("overlay_id_base", 1)),
    )
    reader = Reader(ipc, options=opts)  # deps injected asynchronously below
    if sub_file:  # index an explicit external sub so Alt+←/→/↓ render the target line instantly
        reader.load_sub_index(sub_file)  # (embedded/jimaku tracks: plain sub-seek for now)
    reader.load_deps_async(cfg)
    print(
        f"attached to mpv on {sock} — subs now; coloring/tooltips/mining load in the background. "
        "Ctrl+C to detach (mpv keeps running).",
        flush=True,
    )
    try:
        reader.run()
    finally:
        try:
            reader.close()
            ipc.close()
        except Exception:
            log.debug("attach shutdown cleanup failed", exc_info=True)
    return 0


# `saitenka-overlay <video> …` (no subcommand) behaves like `run` — the legacy invocation shape.
app.default(run)


LOG_PATH = cache_dir() / "overlay.log"


def _setup_logging() -> None:
    """JSON-lines rotating file log (DEBUG) + human-readable WARNING+ to stderr, both redacted.
    The file is what the doctor's "recent errors" section tails and ``report`` bundles;
    log.debug(exc_info=True) calls throughout the codebase land here instead of silent
    except-pass black holes. See :mod:`overlay.app.logsetup` for the structlog pipeline."""
    from overlay.app.logsetup import configure_logging

    configure_logging(LOG_PATH)


def _harden_runtime() -> None:  # pragma: no cover — process-global startup side effects
    """Windows console UTF-8 (so CJK / ✓✗ don't crash cmd.exe) + PATH augmentation for GUI launches."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            except (AttributeError, ValueError):
                pass
    from overlay.mpvio.discover import augment_path

    augment_path()


def _setup_telemetry() -> None:
    """Opt-in only: no-op unless ``[telemetry] enabled = true`` in config. See
    :mod:`overlay.app.telemetry`."""
    from overlay.app.config import load_config, resolve_telemetry
    from overlay.app.telemetry import configure

    configure(resolve_telemetry(load_config()))


def main() -> None:  # pragma: no cover — live-run entry point
    try:
        _ensure_free_threaded()
        _setup_logging()
        _setup_telemetry()
        _harden_runtime()
        from overlay.app.crashlog import install as install_crash_handlers
        from overlay.app.signals import install as install_shutdown_signals

        install_crash_handlers()  # main-thread + worker-thread + faulthandler crash capture
        install_shutdown_signals()  # SIGTERM / SIGBREAK → graceful cleanup (like Ctrl+C)
        override = _argv_config_override(sys.argv[1:])
        if override:  # --config PATH re-points the declarative TOML
            app.config = cyclopts.config.Toml(
                override, must_exist=False, use_commands_as_keys=False, allow_unknown=True
            )
        sys.exit(app())
    except KeyboardInterrupt:
        # Ctrl+C is the documented way to stop the reader. The run/attach loop already tore down mpv,
        # the socket and temp files in its `finally`; swallow the interrupt here so the user sees a
        # clean exit, not a traceback. 130 = 128 + SIGINT, the shell convention.
        sys.exit(130)
    finally:
        from overlay.app.telemetry import shutdown as shutdown_telemetry

        shutdown_telemetry()  # flush + tear down providers; a no-op if never configured


if __name__ == "__main__":
    main()
