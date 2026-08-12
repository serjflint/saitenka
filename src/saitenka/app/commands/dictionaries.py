from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import cyclopts

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from saitenka.app.cli_run import _resolve_names as _resolve_names  # noqa: PLC0414  # re-export
from saitenka.app.cli_run import (
    jimaku_should_fetch as jimaku_should_fetch,  # noqa: PLC0414  # re-export
)


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


def register(app: cyclopts.App) -> None:
    app.command(import_dicts, name="import")
    app.command(import_settings, name="import-settings", alias="import-yomitan")
    app.command(import_dictionaries, name="import-dictionaries")
