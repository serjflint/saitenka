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

import json
import logging
import os
import subprocess
import sys
import sysconfig
import time
from datetime import UTC
from pathlib import Path
from typing import Annotated, Literal

import cyclopts

from overlay import __version__

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from overlay.app.cli_run import _resolve_names as _resolve_names  # noqa: PLC0414
from overlay.app.cli_run import jimaku_should_fetch as jimaku_should_fetch  # noqa: PLC0414
from overlay.app.cli_run import run_impl
from overlay.app.config import TooltipOptions, config_path, load_config
from overlay.app.paths import cache_dir

log = logging.getLogger(__name__)


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
                sys.exit(subprocess.run(argv, check=False).returncode)
            except KeyboardInterrupt:
                # Ctrl+C on the shared console reaches BOTH processes: the child cleans up and exits
                # on its own SIGINT; the parent must not dump a KeyboardInterrupt traceback from
                # subprocess.wait(). Exit quietly with the conventional 130 (128 + SIGINT).
                sys.exit(130)
        os.execv(sys.executable, argv)


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
    return run_impl(
        video,
        config=config,
        sub_file=sub_file,
        slang=slang,
        dicts=dicts,
        translate_key=translate_key,
        start=start,
        jimaku=jimaku,
        jimaku_key=jimaku_key,
        jimaku_title=jimaku_title,
        resync=resync,
        episode=episode,
        width=width,
        height=height,
        fullscreen=fullscreen,
        use_config=use_config,
        demo_word=demo_word,
        demo_translate=demo_translate,
        demo_scroll=demo_scroll,
        bulk=bulk,
        screenshot=screenshot,
        seconds=seconds,
        color=color,
        known=known,
        anki_decks=anki_decks,
        freq=freq,
        pitch=pitch,
        mine=mine,
        mine_deck=mine_deck,
        mine_model=mine_model,
        mine_key=mine_key,
        mine_all_key=mine_all_key,
        preview_key=preview_key,
        no_audio_play=no_audio_play,
        tip_height=tip_height,
        dict_tabs=dict_tabs,
        pause_on_tooltip=pause_on_tooltip,
        prefetch=prefetch,
        auto_translate=auto_translate,
        hover_switch_delay=hover_switch_delay,
    )


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


def _print_telemetry_status(st) -> None:  # pragma: no cover — presentation; state fn is unit-tested
    from overlay.app.telemetry_toggle import INSTALL_HINT

    on = lambda b: "on" if b else "off"  # noqa: E731
    print(f"config [telemetry] enabled : {on(st.config_enabled)}")
    print(f"telemetry extra installed  : {'yes' if st.extra_installed else 'no'}")
    if st.kill_switch:
        print("OTEL_SDK_DISABLED          : ACTIVE — forces telemetry off regardless of config")
    print(f"→ effectively recording    : {'YES' if st.effective else 'no'}")
    if st.config_enabled and not st.effective:
        reason = (
            "OTEL_SDK_DISABLED is set"
            if st.kill_switch
            else "the 'telemetry' extra isn't installed"
        )
        print(f"   enabled in config, but {reason}")
        if not st.extra_installed and not st.kill_switch:
            print(f"   install it: {INSTALL_HINT}")
    print(f"export dir                 : {st.export_dir}")
    if st.trace_exists:
        s = st.trace_path.stat()
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.st_mtime))
        print(f"last trace                 : {st.trace_path} ({s.st_size / 1024:.0f} KiB, {when})")
    else:
        print(f"last trace                 : none yet at {st.trace_path}")


@app.command
def telemetry(
    action: Annotated[
        Literal["status", "enable", "disable"],
        cyclopts.Parameter(help="flip [telemetry] enabled in overlay.toml, or show status"),
    ] = "status",
) -> int:  # pragma: no cover — thin CLI wrapper; set_enabled/telemetry_state are unit-tested
    """Turn runtime telemetry on or off without hand-editing overlay.toml.

    ``enable``/``disable`` flip ``[telemetry] enabled`` (comment-preserving, prior file backed up).
    The OTel SDK it needs is a SEPARATE ``telemetry`` extra — ``enable`` prints the install command if
    it's missing (config flag and dependency are two switches). ``status`` (default) reports both.
    """
    from overlay.app.config import config_path
    from overlay.app.telemetry_toggle import INSTALL_HINT, set_enabled, telemetry_state

    if action == "status":
        _print_telemetry_status(telemetry_state())
        return 0

    enabled = action == "enable"
    verb = "enabled" if enabled else "disabled"
    changed, backup = set_enabled(enabled)
    if changed:
        print(f"telemetry {verb} in {config_path()}")
        if backup:
            print(f"  backed up previous config → {backup}")
    else:
        print(f"telemetry already {verb} in {config_path()}")

    st = telemetry_state()
    if enabled and not st.extra_installed:
        print("\n⚠ the 'telemetry' extra isn't installed — nothing will record until you run:")
        print(f"    {INSTALL_HINT}")
    elif enabled:
        print(f"  trace → {st.trace_path}")
        print(
            "  use the overlay (watch + hover a while), then `saitenka-overlay report` to bundle it"
        )
    elif st.extra_installed:
        print(
            "  (the 'telemetry' extra stays installed — uninstall separately if you want it gone)"
        )
    return 0


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
def reinstall(
    *,
    source: Annotated[
        Literal["auto", "pypi", "github"],
        cyclopts.Parameter(help="where to reinstall from (auto = PyPI, fall back to GitHub)"),
    ] = "auto",
    ref: Annotated[
        str | None,
        cyclopts.Parameter(
            help="GitHub tag/branch to install (e.g. v0.5.0 or main); implies --source github. "
            "Default: the latest RELEASE tag, falling back to main"
        ),
    ] = None,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="don't prompt; just run the reinstall")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; detect_extras/reinstall_attempts are unit-tested
    """Reinstall (pull the latest) preserving your extras. A bare ``uv tool install --reinstall``
    *replaces* the extras set (silently dropping deinflect/telemetry); this detects what's installed
    and keeps it. From PyPI, falling back to GitHub (which also carries the GPL deinflect add-on). The
    GitHub attempt targets the latest RELEASE tag by default — not bleeding-edge main; pass ``--ref
    main`` for that, or ``--ref vX.Y.Z`` to pin a release."""
    import subprocess

    from overlay.app.lifecycle import detect_extras, latest_release_tag, reinstall_attempts

    extras = detect_extras()
    github_ref: str | None = None
    if ref is not None:
        source = "github"  # an explicit --ref pins the GitHub source
        github_ref = ref
    elif source in ("auto", "github"):
        github_ref = latest_release_tag()  # default the GitHub attempt to the latest release
        print(f"latest release: {github_ref}" if github_ref else "no release info — using main")
    attempts = reinstall_attempts(extras, source=source, github_ref=github_ref)
    print(f"detected extras: {', '.join(extras) or '(none)'}")
    if not yes and input(
        f"Reinstall keeping [{','.join(extras) or 'none'}]? [y/N] "
    ).strip().lower() not in ("y", "yes"):
        print("cancelled")
        return 1
    rc = 1
    for i, cmd in enumerate(attempts):
        print("  $", " ".join(cmd))
        rc = subprocess.run(cmd, check=False).returncode
        if rc == 0:
            return 0
        if i + 1 < len(attempts):
            print(f"  that source failed (exit {rc}) — trying the next…")
    return rc


@app.command
def uninstall(
    *,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="don't prompt; delete without confirming")
    ] = False,
    keep_dicts: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(), help="keep the (expensive) dictionary DB; remove everything else"
        ),
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; the removal logic is unit-tested in test_lifecycle
    """Delete saitenka's config, dictionaries, cache/logs, crash reports and mpv plugin. Leaves mpv and
    ffmpeg installed. Does NOT remove the saitenka-overlay binary itself — the last line tells you how."""
    from overlay.app.lifecycle import uninstall as do_uninstall

    confirm = (
        (lambda _p: True)
        if yes
        else (lambda p: input(f"{p} [y/N] ").strip().lower() in ("y", "yes"))
    )
    removed = do_uninstall(confirm, keep_dicts=keep_dicts)
    if not removed:
        print("nothing removed (no saitenka data found, or cancelled)")
    else:
        for d in removed:
            print(f"  removed {d}")
    print("mpv / ffmpeg left untouched.")
    print("To remove the app itself:  uv tool uninstall saitenka-overlay")
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
            banded=bool(cfg.get("banded", _tt.banded)),
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
            prefetch_lookahead=cfg.get("prefetch_lookahead", _po.prefetch_lookahead),
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
