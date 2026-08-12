from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal

import cyclopts

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from saitenka.app.cli_run import _resolve_names as _resolve_names  # noqa: PLC0414  # re-export
from saitenka.app.cli_run import (
    jimaku_should_fetch as jimaku_should_fetch,  # noqa: PLC0414  # re-export
)
from saitenka.app.config import config_path, load_config

if TYPE_CHECKING:
    from saitenka.app.prewarm import PrewarmPlan, PrewarmProgress, PrewarmResult
from saitenka.app.command_defaults import _MINE_MODEL_DEFAULT, _mine_cfg

# --- setup / maintenance subcommands ---------------------------------------------------------------


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


def register(app: cyclopts.App) -> None:
    for command in (doctor, telemetry, prewarm, stats, report):
        app.command(command)
