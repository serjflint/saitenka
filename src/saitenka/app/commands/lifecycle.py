from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

import cyclopts

# _resolve_names/jimaku_should_fetch: re-exported — tests import them from here directly.
from saitenka.app.launch.run import _resolve_names as _resolve_names  # noqa: PLC0414  # re-export
from saitenka.app.launch.run import (
    jimaku_should_fetch as jimaku_should_fetch,  # noqa: PLC0414  # re-export
)


def install_plugin() -> int:  # pragma: no cover — thin CLI wrapper; plugin ops are unit-tested
    """Install the saitenka.lua mpv user-script (plugin mode)."""
    from saitenka.app.plugin import install_plugin as do_install

    dest = do_install()
    print(f"installed {dest}")
    print("mpv will now spawn `saitenka attach <socket>` on file-loaded, from any launcher.")
    return 0


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


def register(app: cyclopts.App) -> None:
    app.command(install_plugin, name="install-plugin")
    app.command(uninstall_plugin, name="uninstall-plugin")
    for command in (update, reinstall, uninstall):
        app.command(command)
    app.command(setup, alias="install")
