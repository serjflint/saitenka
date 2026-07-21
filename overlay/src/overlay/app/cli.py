"""The real overlay entrypoint: a cyclopts CLI with ``run`` as the default command.

``examples/mpv_reader.py`` is now a thin wrapper around this module. HARD CONSTRAINT: every legacy
mpv_reader.py flag keeps its exact name and repeatable/negation behaviour (RUNNING.md is the
contract; ``tests/test_cli.py`` pins the inventory). The config file feeds defaults declaratively
via ``cyclopts.config.Toml`` (precedence: defaults < file < explicit CLI flags); the legacy-named
keys (``dicts``/``freq``/``pitch``/``known``/``[mine]``) are mapped explicitly, exactly as the old
argparse two-phase parse did.

Subcommands: ``doctor``, ``init``, ``import-yomitan``, ``install-plugin`` / ``uninstall-plugin``,
``attach`` (joins a running mpv and selects the JP sub track / fetches jimaku), and ``setup``.
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

from overlay.app.config import config_path, load_config

log = logging.getLogger(__name__)

DEMO_LINE = "門前の小僧習わぬ経を読む"
DEMO_LINE_EN = "A shop-boy at the temple gate recites sutras he was never taught."


def _ensure_free_threaded() -> None:
    """Adopt the free-threaded runtime: on a 3.14t build force the GIL OFF before fugashi's
    C extension loads (it hasn't declared FT-safety and would re-enable the GIL). Re-exec once so
    PYTHON_GIL=0 is set before the interpreter finishes starting. No-op on a standard build.

    Always re-exec via ``-m overlay.app.cli`` — NEVER via ``sys.argv[0]``: under ``python -m``,
    argv[0] is this file's path, and running it script-style would put ``src/overlay/app/`` first
    on sys.path, where our ``tokenize.py`` shadows the stdlib module and breaks the interpreter."""
    if sysconfig.get_config_var("Py_GIL_DISABLED") and os.environ.get("PYTHON_GIL") != "0":
        os.environ["PYTHON_GIL"] = "0"
        os.execv(sys.executable, [sys.executable, "-m", "overlay.app.cli", *sys.argv[1:]])


def _resolve_paths(flag_vals: list[str] | None, cfg: dict, key: str) -> list[str]:
    """Flag values win over the config file, and BOTH sides get ~/$VAR expansion — TOML values
    reach the flag parameter via cyclopts config.Toml, so expanding only the cfg fallback would
    leave literal '~' paths to fail at zip-open time."""
    from overlay.app.config import expand_paths

    return expand_paths(list(flag_vals or []) or cfg.get(key) or [])


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
        cyclopts.Parameter(help="settings TOML (default ~/.config/saitenka/overlay.toml)"),
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
            help="Yomitan dictionary .zip (repeatable; ordered — first = top of the tooltip)",
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
            help="Yomitan frequency dict .zip (repeatable; green pills + coloring bands)",
        ),
    ] = None,
    pitch: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(), help="Yomitan pitch-accent dict .zip (repeatable; purple pills)"
        ),
    ] = None,
    mine: Annotated[
        bool, cyclopts.Parameter(negative=(), help="enable one-key mining to Anki")
    ] = False,
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
            help="max tooltip height as a fraction of the video height (default 0.6)"
        ),
    ] = 0.6,
    pause_on_tooltip: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(), help="auto-pause playback while a tooltip is shown (resumes when it hides)"
        ),
    ] = False,
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

    cfg = load_config(config)

    # resolve dict/freq/pitch lists: explicit CLI flags win, else fall back to the config file
    dict_paths = _resolve_paths(dicts, cfg, "dicts")
    freq_paths = _resolve_paths(freq, cfg, "freq")
    pitch_paths = _resolve_paths(pitch, cfg, "pitch")
    known_cfg = json.loads(anki_decks) if anki_decks else cfg.get("known")

    if not (color or known_cfg or known or dict_paths or mine):
        print(
            "[hint] bare demo: no coloring, no monolingual dicts, no mining. Put your dicts in\n"
            "       ~/.config/saitenka/overlay.toml once (see overlay.example.toml), or pass\n"
            '       --dict … --freq … --pitch … --anki-decks \'{"Saitenka::Known":["Expression"]}\'\n'
            "       --mine  (see RUNNING.md §3)."
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

    dict_set = None
    if dict_paths:
        from overlay.app.dictionary import DictionarySet

        print(
            f"loading {len(dict_paths)} dict(s) · {len(freq_paths)} freq · {len(pitch_paths)} "
            "pitch… (first run builds a cache)"
        )
        dict_set = DictionarySet.load(dict_paths, freq_paths=freq_paths, pitch_paths=pitch_paths)
        print("dictionaries:", [d.title for d in dict_set.dicts])
        if dict_set.freqs:
            print("frequency:", [f.title for f in dict_set.freqs])
        if dict_set.pitches:
            print("pitch:", [p.title for p in dict_set.pitches])

    scorer = None
    if color or known or known_cfg or freq_paths:
        from overlay.app.scoring import Scorer
        from overlay.app.wordlists import FreqDict, JlptDict, KnownWords

        if known_cfg:
            kw = KnownWords.from_ankiconnect(known_cfg)
        else:
            kw = KnownWords.from_set([w for w in known.split(",") if w])
        fd = FreqDict.load(freq_paths[0]) if freq_paths else None
        scorer = Scorer(known=kw, freq=fd, jlpt=JlptDict.load())
        print(f"coloring on — known:{len(kw.words)} freq:{bool(fd)} jlpt:on")

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-reader-"))
    dur = max(8, int(seconds))
    video_path = Path(video).expanduser() if video else tmp / "clip.mp4"
    if not video:
        print(f"no video — generating a {width}x{height} test clip…")
        _make_clip(video_path, dur, width, height)

    # subtitle source: explicit file > jimaku fetch > embedded track (--slang) > generated demo line
    sub_path = en_sub_path = None
    if sub_file:
        sub_path = Path(sub_file).expanduser()
    elif jimaku:
        from overlay.app.jimaku import JimakuClient, JimakuError, parse_filename

        title, ep = parse_filename(video_path)
        title = jimaku_title or title
        ep = episode if episode is not None else ep
        print(f"jimaku: fetching subs for {title!r} ep {ep}…")
        try:
            sub_path = JimakuClient(jimaku_key).fetch(title, ep, tmp)
            print("jimaku: got", sub_path.name)
            if resync and video_path.exists():
                from overlay.app.resync import maybe_resync

                print("jimaku: resyncing…")
                sub_path = maybe_resync(video_path, sub_path, enabled=True)
                print("jimaku: resync →", sub_path.name)
        except JimakuError as e:
            print("jimaku failed:", e, "— falling back to embedded/default", file=sys.stderr)
    elif not video:
        sub_path = tmp / "line.srt"
        _make_srt(sub_path, dur, DEMO_LINE)
        en_sub_path = tmp / "line.en.srt"  # secondary EN track → test the `t` translation reveal
        _make_srt(en_sub_path, dur, DEMO_LINE_EN)

    from overlay.mpvio.discover import find_mpv

    mpv_bin = find_mpv(cfg.get("mpv_path")) or "mpv"
    sock = str(tmp / "mpv.sock")
    cmd = [
        mpv_bin,
        f"--input-ipc-server={sock}",
        "--force-window=yes",
        "--keep-open=yes",
        f"--slang={slang}",
        "--sub-visibility=no",
        "--osd-level=0",
        "--pause" if screenshot else "--loop-file=inf",
        f"--start={start}",
        str(video_path),
    ]
    if sub_path:
        cmd.insert(-1, f"--sub-file={sub_path}")
    if en_sub_path:
        cmd.insert(-1, f"--sub-file={en_sub_path}")  # loaded as a 2nd track → secondary/translation
    if not use_config:
        cmd.insert(1, "--no-config")
    if fullscreen:
        cmd.insert(1, "--fullscreen")
    print("launching:", " ".join(cmd))
    proc = subprocess.Popen(cmd)

    try:
        ipc = MpvIPC(sock).connect(timeout=15)
    except TimeoutError as e:
        print("mpv IPC unreachable:", e, file=sys.stderr)
        proc.terminate()
        return 2

    from overlay.app.config import (
        KeyOptions,
        MiningOptions,
        ReaderOptions,
        TooltipOptions,
        TranslationOptions,
    )

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
            pause_on_tooltip=pause_on_tooltip,
            hover_switch_delay=hover_switch_delay,
        ),
        mining=MiningOptions(play_audio=not no_audio_play),
        translation=TranslationOptions(auto_translate=auto_translate),
        prefetch=prefetch,
    )
    reader = Reader(
        ipc, scorer=scorer, anki=anki, mine_cfg=mine_conf, dict_set=dict_set, options=opts
    )
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
            proc.terminate()
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
    mine_deck: str = _mine_cfg.get("deck", "Saitenka::Mining"),
    mine_model: str = _mine_cfg.get("model", "Lapis"),
) -> int:  # pragma: no cover — thin CLI wrapper; run_checks/print_report are unit-tested
    """Check the environment: mpv/ffmpeg, config, dict cache, fonts, AnkiConnect."""
    from overlay.app.doctor import print_report, run_checks

    report = run_checks(deck=mine_deck, model=mine_model)
    if json_out:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return report.exit_code


@app.command
def init() -> int:  # pragma: no cover — interactive wizard, exercised live
    """Interactive first-run wizard: discover, propose, write the config."""
    from overlay.app.init_wizard import run_init

    return run_init()


@app.command(name="copy-dicts")
def copy_dicts(
    *,
    config: str | None = None,
) -> int:  # pragma: no cover — thin CLI wrapper; relocate/repoint are unit-tested
    """Copy dictionaries out of TCC-protected folders (Documents/Desktop/Downloads) into
    ~/.local/share/saitenka/dicts and repoint the config, so plugin-mode mpv stops prompting for
    Documents access."""
    from overlay.app.config import dicts_data_dir
    from overlay.app.relocate import relocate_dicts

    mappings = relocate_dicts(config=config)
    if not mappings:
        print("all dictionary paths are already outside protected folders — nothing to copy")
        return 0
    print(f"copied {len(mappings)} dict(s) → {dicts_data_dir()} and repointed the config:")
    for old, new in mappings:
        print(f"  {old} → {new}")
    return 0


@app.command(name="set-jimaku-key")
def set_jimaku_key(
    key: Annotated[
        str | None, cyclopts.Parameter(help="the key (omit to be prompted with hidden input)")
    ] = None,
) -> int:  # pragma: no cover — interactive/secret I/O; keychain_set is unit-tested
    """Store your jimaku.cc API key in the macOS login Keychain.

    The Keychain is readable by a GUI-launched (plugin-mode) mpv, unlike a shell env var — so this
    is the reliable place for the key. Get a free key at jimaku.cc → account → API key.
    """
    import getpass

    from overlay.app.jimaku import keychain_set

    k = (key or getpass.getpass("jimaku.cc API key (hidden): ")).strip()
    if not k:
        print("no key entered", file=sys.stderr)
        return 2
    if keychain_set(k):
        print("stored in the macOS Keychain (service=saitenka-overlay, account=jimaku)")
        return 0
    print(
        "could not store in the Keychain (non-macOS?) — set $JIMAKU_API_KEY or [jimaku].key instead",
        file=sys.stderr,
    )
    return 1


@app.command(name="import-yomitan")
def import_yomitan(
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
    """Import dictionary order + options from a Yomitan settings export."""
    from overlay.app.init_wizard import _ask
    from overlay.app.yomitan_import import YomitanImportError, run_import

    confirm = (lambda _p: True) if yes else _ask
    try:
        return run_import(settings, scan_dir, confirm)
    except YomitanImportError as e:
        print(f"import failed: {e}", file=sys.stderr)
        return 1


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
def setup(
    *,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="answer yes to every prompt")
    ] = False,
    dry_run: Annotated[
        bool, cyclopts.Parameter(negative=(), help="show what would happen, change nothing")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; the wizard steps are unit-tested
    """One-command setup: inventory → install mpv+ffmpeg → doctor → init → import → plugin."""
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
    jm = cfg.get("jimaku") if isinstance(cfg.get("jimaku"), dict) else {}
    jimaku = jimaku or bool(jm.get("enabled", False))
    jimaku_key = jimaku_key or jm.get("key")
    resync = resync and bool(jm.get("resync", True))

    try:
        status = ensure_jp_subs(
            ipc,
            slang=slang,
            sub_file=sub_file,
            jimaku=jimaku,
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

    # Build coloring/dict/mining collaborators from config — without these, attach is a bare
    # subtitle renderer (no known/JLPT coloring, no freq pills, no tooltips, no mining).
    from overlay.app.reader_deps import build_reader_deps

    scorer, anki, mine_conf, dict_set = build_reader_deps(cfg)
    mc = cfg.get("mine") if isinstance(cfg.get("mine"), dict) else {}

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
        tooltip=TooltipOptions(tip_max_frac=cfg.get("tip_height", 0.6)),
        mining=MiningOptions(play_audio=not bool(cfg.get("no_audio_play", False))),
        translation=TranslationOptions(auto_translate=bool(cfg.get("auto_translate", False))),
        overlay_id_base=int(cfg.get("overlay_id_base", 1)),
    )
    reader = Reader(
        ipc, scorer=scorer, anki=anki, mine_cfg=mine_conf, dict_set=dict_set, options=opts
    )
    bits = []
    if scorer:
        bits.append("coloring+underlines")
    if dict_set:
        bits.append(f"{len(dict_set.dicts)} dicts")
    if mine_conf:
        bits.append("mining")
    feats = ", ".join(bits) or "subs only"
    log.info("attach features: %s", feats)  # detached plugin mode — the log is the only sink
    print(
        f"attached to mpv on {sock} [{feats}] — hover words; Ctrl+C to detach (mpv keeps running).",
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


LOG_PATH = Path.home() / ".cache" / "saitenka-overlay" / "overlay.log"


def _setup_logging() -> None:
    """Rotating file log (DEBUG) + WARNING+ to stderr. The file is what the doctor's "recent
    errors" section tails; log.debug(exc_info=True) calls throughout the codebase land here
    instead of silent except-pass black holes."""
    import logging.handlers

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("overlay")
    if root.handlers:  # idempotent (re-exec / tests)
        return
    root.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("[saitenka] %(levelname)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(sh)


def main() -> None:  # pragma: no cover — live-run entry point
    _ensure_free_threaded()
    _setup_logging()
    override = _argv_config_override(sys.argv[1:])
    if override:  # --config PATH re-points the declarative TOML
        app.config = cyclopts.config.Toml(
            override, must_exist=False, use_commands_as_keys=False, allow_unknown=True
        )
    sys.exit(app())


if __name__ == "__main__":
    main()
