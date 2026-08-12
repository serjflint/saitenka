"""The real overlay entrypoint: a cyclopts CLI with ``run`` as the default command.

``examples/mpv_reader.py`` is now a thin wrapper around this module. HARD CONSTRAINT: every legacy
mpv_reader.py flag keeps its exact name and repeatable/negation behaviour — ``tests/test_cli.py`` pins
the inventory (the contract). The config file feeds defaults declaratively
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
import sqlite3
import subprocess
import sys
import sysconfig
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import cyclopts

from saitenka import __version__

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from saitenka.app.cli_run import _resolve_names as _resolve_names  # noqa: PLC0414  # re-export
from saitenka.app.cli_run import default_mine_target, run_impl
from saitenka.app.cli_run import (
    jimaku_should_fetch as jimaku_should_fetch,  # noqa: PLC0414  # re-export
)
from saitenka.app.config import TooltipOptions, config_path, load_config
from saitenka.app.embedded_subs import build_sub_index_for_current_track
from saitenka.app.paths import cache_dir
from saitenka.app.subselect import ProviderConfig

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from saitenka.app.config import ReaderOptions
    from saitenka.app.prewarm import PrewarmPlan, PrewarmProgress, PrewarmResult


def _ensure_free_threaded() -> None:
    """Adopt the free-threaded runtime: on a 3.14t build force the GIL OFF before fugashi's
    C extension loads (it hasn't declared FT-safety and would re-enable the GIL). Re-launch once so
    PYTHON_GIL=0 is set before the interpreter finishes starting. No-op on a standard build.

    Always re-launch via ``-m saitenka.app.cli`` — NEVER via ``sys.argv[0]``: under ``python -m``,
    argv[0] is this file's path, and running it script-style would put ``src/saitenka/app/`` first
    on sys.path, where our ``tokenize.py`` shadows the stdlib module and breaks the interpreter."""
    if sysconfig.get_config_var("Py_GIL_DISABLED") and os.environ.get("PYTHON_GIL") != "0":
        os.environ["PYTHON_GIL"] = "0"
        argv = [sys.executable, "-m", "saitenka.app.cli", *sys.argv[1:]]
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
    from saitenka.app.paths import legacy_dict_artifacts

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


def _resolve_mine_model(mine_cfg: dict) -> str:
    """The mining note type: an explicit ``[mine].model``, else the ``[mine].preset`` name (Lapis/Kiku
    imply their own model), else Lapis. Mirrors what ``_build_mining`` derives on the attach seam, so
    ``run``/``doctor`` target the SAME note type a preset-only config mines to (the both-seams trap).
    Delegates to the shared ``default_mine_target`` so this derivation lives in ONE place."""
    return default_mine_target(mine_cfg)[1]


_MINE_MODEL_DEFAULT = _resolve_mine_model(_mine_cfg)

app = cyclopts.App(
    name="saitenka",
    help="Saitenka in-mpv overlay: JP subs with FSRS coloring, hover → multi-dict tooltip, mining.",
    # Pin the version explicitly — cyclopts otherwise resolves it from the `overlay` import package's
    # metadata, which has no distribution (the dist is `saitenka`), so `--version` printed 0.0.0.
    version=__version__,
    config=cyclopts.config.Toml(
        config_path(), must_exist=False, use_commands_as_keys=False, allow_unknown=True
    ),
)

# `saitenka profile <list|show|add|use|remove>` — reading-profile CRUD (#254 W4). Imported here, not
# at top, because it attaches to `app`, which must exist first.
from saitenka.app.profile_cli import profile_app  # noqa: E402

app.command(profile_app)


@app.command(name="run")
def run(  # noqa: PLR0913  # cyclopts CLI signature — flags are individual params for --help/parsing
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
    profile: Annotated[
        str | None,
        cyclopts.Parameter(help="active reading profile name ([profiles.<name>] in the config)"),
    ] = None,
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
    # None = flag not passed → the active profile's (or runtime [mine]'s) deck/model is resolved at
    # runtime in run_impl (#254). This makes the default honor --config AND a profile, not the
    # import-time default-path config baked into a literal default. An explicit flag still wins.
    mine_deck: str | None = None,
    mine_model: str | None = None,
    mine_normalize_audio: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine-normalize-audio",
            help="normalize mined clip loudness to −23 LUFS (EBU R128) so cards play at an even volume",
        ),
    ] = bool(_mine_cfg.get("normalize_audio", False)),
    mine_animated_screenshot: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine-animated-screenshot",
            help="mine a short animated (motion) WebP clip of the scene instead of a still frame",
        ),
    ] = bool(_mine_cfg.get("animated_screenshot", False)),
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
    mine_preview: Annotated[
        bool,
        cyclopts.Parameter(
            negative="--no-mine-preview",  # on by default → explicit off switch
            help="auto-pop the card-preview panel after a mine (--no-mine-preview mines silently "
            "with a toast instead)",
        ),
    ] = bool(_mine_cfg.get("preview", True)),
    tip_height: Annotated[
        float,
        cyclopts.Parameter(
            help=f"max BASE tooltip height as a fraction of the video height "
            f"(default {TooltipOptions().tip_max_frac})"
        ),
        # The default lives once, on TooltipOptions.tip_max_frac. cyclopts still layers
        # defaults < config < CLI (token-based), so sourcing the floor here changes nothing but DRY.
    ] = TooltipOptions().tip_max_frac,
    tip_scale: Annotated[
        float,
        cyclopts.Parameter(
            help="fixed tooltip crisp render scale (0 = auto from resolution; e.g. 1.5 renders "
            "crisp native glyph masks at 1.5× on any display — a cosmetic preference). Match "
            "`saitenka prewarm --scale` to preload native masks"
        ),
    ] = TooltipOptions().tip_scale,
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
    layout_engine: Annotated[
        Literal["default", "taffy"],
        cyclopts.Parameter(
            help="tooltip block-geometry backend: 'default' (pure-Python) or 'taffy' (the optional "
            "taffylite Rust flexbox engine — needs `pip install 'saitenka[layout-engine]'`; "
            "byte-identical output, falls back to default if the wheel is absent)"
        ),
    ] = TooltipOptions().layout_engine,
    mpv_arg: Annotated[
        list[str] | None,
        cyclopts.Parameter(
            negative=(),
            help="extra raw mpv flag (repeatable; SubMiner-style passthrough). Wins over our own "
            "defaults (force-window/keep-open/slang/sub-visibility/osd-level/start) — mpv is "
            "last-flag-wins — but never over --input-ipc-server/--log-file/the anti-duplicate "
            "script-opts marker, which we always set last",
        ),
    ] = None,
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
        mine_normalize_audio=mine_normalize_audio,
        mine_animated_screenshot=mine_animated_screenshot,
        mine_key=mine_key,
        mine_all_key=mine_all_key,
        preview_key=preview_key,
        no_audio_play=no_audio_play,
        mine_preview=mine_preview,
        tip_height=tip_height,
        tip_scale=tip_scale,
        pause_on_tooltip=pause_on_tooltip,
        prefetch=prefetch,
        auto_translate=auto_translate,
        hover_switch_delay=hover_switch_delay,
        layout_engine=layout_engine,
        mpv_arg=mpv_arg,
        profile=profile,
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
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=("--verbose", "-v"),
            negative=(),
            help="also show informational lines hidden by default (platform, unset sockets, the "
            "full dictionary list, telemetry state)",
        ),
    ] = False,
    mine_deck: str = _mine_cfg.get("deck", "Saitenka::Mining"),
    mine_model: str = _MINE_MODEL_DEFAULT,
) -> int:  # pragma: no cover — thin CLI wrapper; run_checks/print_report are unit-tested
    """Check the environment: mpv/ffmpeg, config, dict cache, fonts, AnkiConnect."""
    from saitenka.app.doctor import print_report, run_checks

    report = run_checks(deck=mine_deck, model=mine_model)
    if json_out:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        print_report(report, summary=summary, verbose=verbose)
    return report.exit_code


def _print_telemetry_status(st) -> None:  # pragma: no cover — presentation; state fn is unit-tested
    from saitenka.app.telemetry_toggle import INSTALL_HINT

    def on(*, enabled: bool) -> str:
        return "on" if enabled else "off"

    print(f"config [telemetry] enabled : {on(enabled=st.config_enabled)}")
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
    from saitenka.app.config import config_path
    from saitenka.app.telemetry_toggle import INSTALL_HINT, set_enabled, telemetry_state

    if action == "status":
        _print_telemetry_status(telemetry_state())
        return 0

    enabled = action == "enable"
    verb = "enabled" if enabled else "disabled"
    changed, backup = set_enabled(enabled=enabled)
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
        print("  use the overlay (watch + hover a while), then `saitenka report` to bundle it")
    elif st.extra_installed:
        print(
            "  (the 'telemetry' extra stays installed — uninstall separately if you want it gone)"
        )
    return 0


def _resolve_atlas_scale(cfg: dict, atlas_scale: float) -> float:
    """Resolve ``--atlas-scale``: ``> 0`` is used as-is; ``<= 0`` inherits the runtime ``tip_scale`` so
    the atlas matches what the tooltip displays. Reads ``tip_scale`` EXACTLY as the runtime does — the
    top-level ``cfg["tip_scale"]``, NOT a nested ``[tooltip]`` key the runtime ignores — so prewarm and
    runtime can never disagree. Falls back to 1.0 (reference only) when it's unset."""
    if atlas_scale > 0:
        return atlas_scale
    return float(cfg.get("tip_scale") or 0.0) or 1.0


def _prewarm_emit_start(
    plan: PrewarmPlan, *, atlas_only: bool, atlas_scale: float, width: int, height: int
) -> None:  # pragma: no cover — presentation for the thin CLI wrapper
    if atlas_only:
        builds = (
            "1× reference masks"
            if atlas_scale <= 1.0
            else f"1× reference + {atlas_scale:g}× native masks"
        )
        print(
            f"prewarm(atlas): {plan.total:,} words @scale {atlas_scale:g} — building {builds} · "
            f"{plan.already_done:,} already done → {plan.remaining:,} to raster · "
            f"atlas {plan.nbytes / 1e6:.0f} MB on disk (uncapped)",
            flush=True,
        )
    else:
        print(
            f"prewarm: {plan.total:,} words @{width}×{height} · "
            f"render cache {plan.nbytes / 1e6:.0f} MB on disk (byte-ceiling bounded)",
            flush=True,
        )


def _prewarm_emit_progress(
    p: PrewarmProgress, *, atlas_only: bool
) -> None:  # pragma: no cover — presentation for the thin CLI wrapper
    if not atlas_only:
        print(
            f"  … measured {p.measured:,} words → {p.rows:,} heads, {p.nbytes / 1e6:.0f} MB",
            flush=True,
        )
        return
    proj = f" · ~{p.projected_bytes / 1e6:.0f} MB projected" if p.projected_bytes else ""
    print(
        f"  … {p.measured:,}/{p.to_raster:,} rastered · {p.skipped:,} skipped · "
        f"+{p.new_rows:,} new ({p.dup_masks:,} already cached) "
        f"→ {p.rows:,} masks, {p.nbytes / 1e6:.0f} MB{proj}",
        flush=True,
    )


def _prewarm_emit_summary(
    result: PrewarmResult, *, atlas_only: bool
) -> None:  # pragma: no cover — presentation for the thin CLI wrapper
    if atlas_only:
        tail = " · stopped early (plateau)" if result.stopped_at_ceiling else ""
        print(
            f"prewarm(atlas): rendered {result.candidates:,} words "
            f"({result.skipped:,} skipped via resume) → {result.rows:,} masks, "
            f"{result.bytes / 1e6:.0f} MB on disk{tail}"
        )
    else:
        tail = ", stopped at the byte ceiling)" if result.stopped_at_ceiling else ")"
        print(
            f"prewarm: rendered {result.candidates:,} popular words → stored {result.stored:,} "
            f"non-trivial heads ({result.rows:,} rows, {result.bytes / 1e6:.0f} MB on disk{tail}"
        )


@app.command
def prewarm(
    width: Annotated[
        int, cyclopts.Parameter(help="video/window width in px (your play resolution)")
    ] = 1920,
    height: Annotated[int, cyclopts.Parameter(help="video/window height in px")] = 1080,
    limit: Annotated[
        int,
        cyclopts.Parameter(
            help="how many of the most-frequent words to prebuild (popularity cap; 0 = ALL freq-ranked "
            "words — full mask-atlas coverage, the render cache stays bounded by its byte ceiling)"
        ),
    ] = 32000,
    workers: Annotated[
        int, cyclopts.Parameter(help="parallel render threads (0 = auto, ~cpu count)")
    ] = 0,
    atlas_scale: Annotated[
        float,
        cyclopts.Parameter(
            help="display scale for the crisp tooltip. EVERY run builds the 1× reference masks; a scale "
            ">1 ALSO builds native masks at that scale (so one --atlas-scale 1.5 run covers 1.0 AND 1.5 — "
            "you do NOT need a separate 1.0 run). Match your runtime tip_scale; 0 = read it from config "
            "(top-level tip_scale). 1.0 = reference only. The render cache stays 1×-reference-only (#149)"
        ),
    ] = 0.0,
    *,
    atlas_only: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="fill ONLY the glyph mask atlas (leave the render cache untouched) — pair with "
            "`--limit 0` to saturate the atlas over the whole corpus without growing the render cache",
        ),
    ] = False,
    atlas_plateau: Annotated[
        int,
        cyclopts.Parameter(
            help="atlas-only: stop early after this many consecutive heartbeats add essentially no new "
            "masks. The CJK glyph set saturates in the first few thousand words, so the rest of a "
            "`--limit 0` (>1M-word) sweep is near-pure churn. 0 = off (raster every word)",
        ),
    ] = 0,
) -> int:  # pragma: no cover — thin CLI wrapper; prewarm() logic is unit-tested
    """Prebuild the persistent tooltip render cache (#149) so even a FIRST-session cold hover on a
    pathological word is instant (copy+upload), not a 40–170 ms build+raster.

    Renders the top ``--limit`` most-frequent words (episode-agnostic, population-aware) at ``--width``
    ×``--height`` — MUST match your play resolution, or a live hover computes a different key and misses —
    most-popular first, until the ``[tooltip] render_cache_max_mb`` byte ceiling is reached. Needs
    ``[tooltip] render_cache`` on and dictionaries imported. Re-run after a resolution or dictionary
    change (both invalidate the cache signature).
    """
    from functools import partial

    from saitenka.app.config import load_config
    from saitenka.app.prewarm import PrewarmOptions
    from saitenka.app.prewarm import prewarm as _prewarm

    cfg = load_config()
    atlas_scale = _resolve_atlas_scale(cfg, atlas_scale)
    if (
        not atlas_only
        and not load_config().get("tooltip", {}).get("render_cache")
        and not load_config().get("render_cache")
    ):
        print(
            "note: [tooltip] render_cache is off — prewarm still builds the cache, but enable it to use it"
        )

    try:
        result = _prewarm(
            width,
            height,
            limit,
            on_progress=partial(_prewarm_emit_progress, atlas_only=atlas_only),
            workers=workers,
            opts=PrewarmOptions(
                atlas_only=atlas_only,
                atlas_scale=atlas_scale,  # already resolved to an effective scale (≥ 1.0)
                plateau_stop=atlas_plateau,
            ),
            on_start=partial(
                _prewarm_emit_start,
                atlas_only=atlas_only,
                atlas_scale=atlas_scale,
                width=width,
                height=height,
            ),
        )
    except RuntimeError as e:
        print(f"prewarm: {e}")
        return 1
    _prewarm_emit_summary(result, atlas_only=atlas_only)
    return 0


@app.command
def stats(
    limit: Annotated[int, cyclopts.Parameter(help="number of recent sessions to show")] = 20,
) -> int:
    """Show local immersion-session history."""
    from saitenka.app.session_stats import SessionStore, summary

    try:
        store = SessionStore()
        rows = store.recent(limit)
        store.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"session history unavailable: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("No immersion sessions recorded. Enable [stats] in overlay.toml to start recording.")
        return 0
    for row in rows:
        stamp = datetime.fromtimestamp(row.started_at, UTC).astimezone().strftime("%Y-%m-%d %H:%M")
        state = "complete" if row.completed else "incomplete"
        print(f"{stamp}  {row.media_name or '(unknown media)'}  [{state}]  {summary(row)}")
    return 0


@app.command(
    show=False
)  # low-level primitive — end users run `setup` (which calls this); hidden from help
def init() -> int:  # pragma: no cover — interactive wizard, exercised live
    """Write a starter config (the config-file primitive `setup` builds on). Prefer `setup`/`install`."""
    from saitenka.app.init_wizard import run_init

    return run_init()


@app.command
def config() -> (
    int
):  # pragma: no cover — interactive editor; the schema/coercion core is unit-tested
    """Interactively edit ``overlay.toml``: pick a section → an option → a typed field, comment-preserving.

    Offers the current value (or the built-in default) and round-trips through tomlkit, so every other
    key + comment survives. On a non-tty it writes nothing (the prompts return their defaults)."""
    from saitenka.app.config_editor import run_editor

    return run_editor()


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

    from saitenka.app import prompt
    from saitenka.app.progress import BuildBar
    from saitenka.app.yomitan_import import finalize_import, gather_yomitan_zips, import_zips

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
    finalize_import(cfg, confirm=(lambda _p: True) if yes else prompt.confirm)
    _print_legacy_note()
    return 0


@app.command(name="set-jimaku-key")
def set_jimaku_key(
    key: Annotated[
        str | None, cyclopts.Parameter(help="the key (omit to be prompted with hidden input)")
    ] = None,
    *,
    file: Annotated[
        bool,
        cyclopts.Parameter(
            help="store in the owner-only file, skipping the OS keyring (and persist that opt-out) — "
            "for Windows AV that flags the first Credential Locker read"
        ),
    ] = False,
    verify: Annotated[
        bool, cyclopts.Parameter(help="test the key against jimaku.cc right after saving")
    ] = True,
) -> int:  # pragma: no cover — interactive/secret I/O; store/verify helpers are unit-tested
    """Store your jimaku.cc API key where a plugin-mode (GUI-launched) mpv can read it.

    Uses the OS keyring when available, else an owner-only file beside overlay.toml (force the file with
    ``--file``). Either beats a shell env var, which a GUI-launched mpv can't see. Get a free key at
    https://jimaku.cc/account (API docs: https://jimaku.cc/api/docs).

    Windows paste tip: the hidden prompt does NOT accept Ctrl+V (it captures one control char), so a
    pasted key can silently truncate to a single character. Right-click to paste at the prompt, or pass
    the key as an argument on the normal command line where Ctrl+V works: ``set-jimaku-key <key>``.
    """
    import getpass

    from saitenka.app.init_wizard import store_jimaku_key
    from saitenka.app.jimaku import key_paste_warning, prompt_for_key

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
    method, backup = store_jimaku_key(k, prefer_file=file)
    if method == "keyring":
        print("stored in the OS secret store (Keychain / Credential Locker / Secret Service)")
    else:
        from saitenka.app.jimaku import key_file_path

        print(f"stored in {key_file_path()} (plaintext, owner-only)")
        if backup:
            print(f"backed up existing config → {backup}")
    return _verify_saved_jimaku_key(k) if verify else 0


def _verify_saved_jimaku_key(
    k: str,
) -> int:  # pragma: no cover — thin wrapper; verify_key is tested
    """Best-effort post-save probe. Catches the wrong-but-full-length key the length guard can't (a
    typo / revoked / wrong-account key that fails silently mid-video otherwise). A definite rejection
    is loud + non-zero; a network hiccup never fails a correctly-saved key."""
    from saitenka.app.jimaku import verify_key

    status, msg = verify_key(k)
    if status == "ok":
        print(f"verified: {msg}")
        return 0
    if status == "bad":
        print(f"WARNING: jimaku REJECTED the saved key — {msg}", file=sys.stderr)
        print(
            "re-copy the full key from https://jimaku.cc/account (right-click to paste at the hidden "
            "prompt), then re-run set-jimaku-key.",
            file=sys.stderr,
        )
        return 3
    print(f"note: couldn't verify now ({msg}) — saved anyway.", file=sys.stderr)
    return 0


@app.command(name="jimaku-check")
def jimaku_check(
    query: Annotated[str, cyclopts.Parameter(help="anime title to test-search")] = "Spy x Family",
) -> int:  # pragma: no cover — thin CLI wrapper; JimakuClient is tested
    """Diagnose jimaku without launching a video: resolve the key and run a test search, printing the
    exact outcome (key found? 200 OK / 401 bad key / 400 + server message / network error)."""
    from saitenka.app.jimaku import resolve_jimaku_key, verify_key

    key, src = resolve_jimaku_key()
    if not key:
        print("jimaku key: NOT configured — run `saitenka set-jimaku-key`", file=sys.stderr)
        return 1
    print(f"jimaku key: found (from {src}), {len(key)} chars")
    status, msg = verify_key(key, query)
    if status == "ok":
        print(f"search {query!r}: OK — {msg}")
        return 0
    print(f"search {query!r}: {msg}", file=sys.stderr)
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
    from saitenka.app import prompt
    from saitenka.app.yomitan_import import YomitanImportError, run_import

    confirm = (lambda _p: True) if yes else prompt.confirm
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

    from saitenka.app import prompt
    from saitenka.app.progress import BuildBar
    from saitenka.app.yomitan_db_import import YomitanDbImportError, import_database, read_header
    from saitenka.app.yomitan_import import finalize_import, import_zips

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

    finalize_import(cfg, confirm=(lambda _p: True) if yes else prompt.confirm)
    return 0


@app.command(name="install-plugin")
def install_plugin() -> int:  # pragma: no cover — thin CLI wrapper; plugin ops are unit-tested
    """Install the saitenka.lua mpv user-script (plugin mode)."""
    from saitenka.app.plugin import install_plugin as do_install

    dest = do_install()
    print(f"installed {dest}")
    print("mpv will now spawn `saitenka attach <socket>` on file-loaded, from any launcher.")
    return 0


@app.command(name="uninstall-plugin")
def uninstall_plugin() -> int:  # pragma: no cover — thin CLI wrapper; plugin ops are unit-tested
    """Remove the saitenka.lua mpv user-script (backs it up first)."""
    from saitenka.app.plugin import uninstall_plugin as do_uninstall

    backup = do_uninstall()
    if backup is None:
        print("saitenka.lua was not installed — nothing to do")
    else:
        print(f"removed saitenka.lua (backup at {backup})")
    return 0


def _spawn_handoff(
    script: str,
) -> None:  # pragma: no cover — Windows-only detached spawn side effect
    """Write the handoff ``.cmd`` to a temp file and launch it fully detached in its own console, then
    return so ``main`` can exit and release the venv lock. ``CREATE_NEW_CONSOLE`` (not
    ``DETACHED_PROCESS`` — mutually exclusive) gives the user a visible progress window;
    ``close_fds`` keeps the child from inheriting handles that would re-lock the venv."""
    import os
    import subprocess
    import tempfile

    path = Path(tempfile.gettempdir()) / f"saitenka-update-{os.getpid()}.cmd"
    with path.open("w", encoding="ascii", newline="") as f:
        f.write(script)
    if sys.platform == "win32":  # guard so basedpyright sees the win32-only creationflags
        flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["cmd", "/c", path], creationflags=flags, close_fds=True)


def _dispatch_install(attempts: list[list[str]], *, now: bool, label: str) -> int:
    """Run one of ``attempts`` (first that succeeds). POSIX has no exe lock → run in place. Windows
    can't replace a running uv-tool venv, so default to print-and-exit (the user runs it in a fresh
    shell); ``--now`` hands off to a detached updater that waits for THIS process to exit first."""
    import subprocess

    if sys.platform == "win32":  # a running uv-tool process can't delete its own locked venv
        from saitenka.app.lifecycle import handoff_script, resolve_uv

        if not now:
            print(f"Windows can't {label} saitenka while it's running. Run this in a fresh shell:")
            for cmd in attempts:
                print("   ", " ".join(cmd))
            print(
                f"(or re-run `saitenka {label} --now` to hand off to a background updater window)"
            )
            return 0
        import os

        resolved = [[resolve_uv(), *cmd[1:]] for cmd in attempts]
        _spawn_handoff(handoff_script(resolved, os.getpid()))
        print(f"{label} handed off to a background window; this process will now exit.")
        sys.exit(0)
    rc = 1  # POSIX: no exe lock — run in place, trying each attempt until one succeeds
    for i, cmd in enumerate(attempts):
        print("  $", " ".join(cmd))
        rc = subprocess.run(cmd, check=False).returncode
        if rc == 0:
            return 0
        if i + 1 < len(attempts):
            print(f"  that source failed (exit {rc}) — trying the next…")
    return rc


@app.command
def update(
    *,
    now: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="on Windows, hand off to a detached updater instead of printing the cmd",
        ),
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; _dispatch_install/update_command are unit-tested
    """Update saitenka to the latest, keeping your extras (wraps ``uv tool upgrade``, which preserves
    the recorded extras/constraints). On Windows a running tool can't replace its own venv, so this
    prints the command to run in a fresh shell; ``--now`` hands off to a detached updater that waits
    for this process to exit. To CHANGE extras or install from GitHub, use ``reinstall`` instead."""
    from saitenka.app.lifecycle import update_command

    return _dispatch_install([update_command()], now=now, label="update")


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
    now: Annotated[
        bool,
        cyclopts.Parameter(
            negative=(),
            help="on Windows, hand off to a detached updater instead of printing the cmd",
        ),
    ] = False,
    yes: Annotated[
        bool, cyclopts.Parameter(negative=(), help="don't prompt; just run the reinstall")
    ] = False,
) -> int:  # pragma: no cover — thin CLI wrapper; detect_extras/reinstall_attempts are unit-tested
    """Reinstall to CHANGE your extras or source, preserving what's installed. A bare ``uv tool install
    --reinstall`` *replaces* the extras set (silently dropping deinflect/telemetry); this detects
    what's installed and keeps it. From PyPI, falling back to GitHub (which also carries the GPL
    deinflect add-on). The GitHub attempt targets the latest RELEASE tag by default — not bleeding-edge
    main; pass ``--ref main`` for that, or ``--ref vX.Y.Z`` to pin a release. For a plain "get the
    latest" with no extras change, prefer ``update``."""
    from saitenka.app.lifecycle import detect_extras, latest_release_tag, reinstall_attempts

    extras = detect_extras()
    github_ref: str | None = None
    if ref is not None:
        source = "github"  # an explicit --ref pins the GitHub source
        github_ref = ref
    elif source in {"auto", "github"}:
        github_ref = latest_release_tag()  # default the GitHub attempt to the latest release
        print(f"latest release: {github_ref}" if github_ref else "no release info — using main")
    attempts = reinstall_attempts(extras, source=source, github_ref=github_ref)
    print(f"detected extras: {', '.join(extras) or '(none)'}")
    from saitenka.app import prompt

    if not yes and not prompt.confirm(f"Reinstall keeping [{','.join(extras) or 'none'}]?"):
        print("cancelled")
        return 1
    return _dispatch_install(attempts, now=now, label="reinstall")


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
    ffmpeg installed. Does NOT remove the saitenka binary itself — the last line tells you how."""
    from saitenka.app import prompt
    from saitenka.app.lifecycle import uninstall as do_uninstall

    confirm = (lambda _p: True) if yes else prompt.confirm
    removed = do_uninstall(confirm, keep_dicts=keep_dicts)
    if not removed:
        print("nothing removed (no saitenka data found, or cancelled)")
    else:
        for d in removed:
            print(f"  removed {d}")
    print("mpv / ffmpeg left untouched.")
    print("To remove the app itself:  uv tool uninstall saitenka")
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
    from saitenka.app.report import build_report_bundle

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
    from saitenka.app.setup_wizard import run_setup

    return run_setup(yes=yes, dry_run=dry_run)


def _build_attach_options(cfg: dict, *, mine: dict) -> ReaderOptions:
    from saitenka.app.config import (
        KeyOptions,
        MiningOptions,
        PanelOptions,
        PerfOptions,
        ReaderOptions,
        StatsOptions,
        TooltipOptions,
        TranslationOptions,
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
        overlay_id_base=int(cfg.get("overlay_id_base", 1)),
    )


def _finish_attach_subtitle_startup(
    reader, ipc, startup, cfg: ProviderConfig, *, fetch_in_background: tuple[str, ...]
) -> None:
    if startup is not None:
        reader.configure_subtitle_mode(startup, slang=cfg.slang)
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
    from saitenka.app.reader_context import EpisodeContext
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
        session_stats.finish(reader)  # close the finished episode's row before the recorder resets
        reader.episode = EpisodeContext()  # one rebind → no prior-episode state leaks
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


@app.command
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
    from saitenka.app.profiles import resolve_launch_identity
    from saitenka.app.reader_deps import warm_tokenizer

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
    threading.Thread(
        target=warm_tokenizer,
        args=(active_profile.tokenizer,),
        name="saitenka-tokenizer-warm",
        daemon=True,
    ).start()

    from saitenka.app.cli_run import setup_session_telemetry
    from saitenka.app.controller import Reader
    from saitenka.mpvio.ipc import MpvIPC, default_attach_ipc_path

    setup_session_telemetry(cfg)  # capture is per reader session, not global (see cli.main note)
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

    try:
        ipc = MpvIPC(sock).connect(timeout=15)
    except TimeoutError as e:
        print(f"could not attach to mpv IPC at {sock}: {e}", file=sys.stderr)
        return 2

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
    reader = Reader(ipc, options=opts, profile=active_profile)  # deps injected asynchronously below
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


# `saitenka <video> …` (no subcommand) behaves like `run` — the legacy invocation shape.
app.default(run)


LOG_PATH = cache_dir() / "overlay.log"


def _setup_logging() -> None:
    """JSON-lines rotating file log (DEBUG) + human-readable WARNING+ to stderr, both redacted.
    The file is what the doctor's "recent errors" section tails and ``report`` bundles;
    log.debug(exc_info=True) calls throughout the codebase land here instead of silent
    except-pass black holes. See :mod:`saitenka.app.logsetup` for the structlog pipeline."""
    from saitenka.app.logsetup import configure_logging

    configure_logging(LOG_PATH)


def _harden_runtime() -> None:  # pragma: no cover — process-global startup side effects
    """Windows console UTF-8 (so CJK / ✓✗ don't crash cmd.exe) + PATH augmentation for GUI launches."""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            except (AttributeError, ValueError):
                pass
    from saitenka.mpvio.discover import augment_path

    augment_path()


def main() -> None:  # pragma: no cover — live-run entry point
    try:
        _ensure_free_threaded()
        _setup_logging()
        # Telemetry CAPTURE is stood up per reader session (run/attach → cli_run.setup_session_telemetry),
        # NOT here — a one-shot command like `report` would otherwise truncate the session's trace.json.
        _harden_runtime()
        from saitenka.app.crashlog import install as install_crash_handlers
        from saitenka.app.signals import install as install_shutdown_signals

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
        # Guard the import: a reinstall can swap site-packages out from under a live process, and this
        # runs at EVERY exit — an unguarded ImportError here would mask the real exit code/exception
        # with a confusing traceback. shutdown() is already a no-op if telemetry was never configured.
        try:
            from saitenka.app.telemetry import shutdown as shutdown_telemetry
        except ImportError:
            pass
        else:
            shutdown_telemetry()  # flush + tear down providers


if __name__ == "__main__":
    main()
