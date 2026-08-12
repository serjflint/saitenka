"""Saitenka's process entry point and Cyclopts composition root."""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig

import cyclopts

from saitenka import __version__
from saitenka.app.commands import attach as attach_commands
from saitenka.app.commands import configuration as configuration_commands
from saitenka.app.commands import diagnostics as diagnostics_commands
from saitenka.app.commands import dictionaries as dictionary_commands
from saitenka.app.commands import lifecycle as lifecycle_commands
from saitenka.app.commands import run as run_commands
from saitenka.app.commands.diagnostics import (
    _resolve_atlas_scale as _resolve_atlas_scale,  # noqa: PLC0414
)
from saitenka.app.config import config_path
from saitenka.app.launch.run import _resolve_names as _resolve_names  # noqa: PLC0414
from saitenka.app.launch.run import jimaku_should_fetch as jimaku_should_fetch  # noqa: PLC0414
from saitenka.app.paths import cache_dir
from saitenka.app.profile_cli import profile_app


def _ensure_free_threaded() -> None:
    """Re-exec a free-threaded build with the GIL disabled before extension imports."""
    if sysconfig.get_config_var("Py_GIL_DISABLED") and os.environ.get("PYTHON_GIL") != "0":
        os.environ["PYTHON_GIL"] = "0"
        argv = [sys.executable, "-m", "saitenka.app.cli", *sys.argv[1:]]
        if sys.platform == "win32":
            try:
                sys.exit(subprocess.run(argv, check=False).returncode)
            except KeyboardInterrupt:
                sys.exit(130)
        os.execv(sys.executable, argv)


def _argv_config_override(argv: list[str]) -> str | None:
    for index, argument in enumerate(argv):
        if argument == "--config" and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith("--config="):
            return argument.split("=", 1)[1]
    return None


def create_app() -> cyclopts.App:
    app = cyclopts.App(
        name="saitenka",
        help="Saitenka in-mpv overlay: JP subs with FSRS coloring, hover → multi-dict tooltip, mining.",
        version=__version__,
        config=cyclopts.config.Toml(
            config_path(), must_exist=False, use_commands_as_keys=False, allow_unknown=True
        ),
    )
    app.command(profile_app)
    for command_group in (
        run_commands,
        attach_commands,
        dictionary_commands,
        diagnostics_commands,
        configuration_commands,
        lifecycle_commands,
    ):
        command_group.register(app)
    return app


app = create_app()
LOG_PATH = cache_dir() / "overlay.log"


def _setup_logging() -> None:
    from saitenka.app.logsetup import configure_logging

    configure_logging(LOG_PATH)


def _harden_runtime() -> None:  # pragma: no cover
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            except (AttributeError, ValueError):
                pass
    from saitenka.mpvio.discover import augment_path

    augment_path()


def main() -> None:  # pragma: no cover
    try:
        _ensure_free_threaded()
        _setup_logging()
        _harden_runtime()
        from saitenka.app.crashlog import install as install_crash_handlers
        from saitenka.app.signals import install as install_shutdown_signals

        install_crash_handlers()
        install_shutdown_signals()
        override = _argv_config_override(sys.argv[1:])
        if override:
            app.config = cyclopts.config.Toml(
                override, must_exist=False, use_commands_as_keys=False, allow_unknown=True
            )
        sys.exit(app())
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        try:
            from saitenka.app.telemetry import shutdown as shutdown_telemetry
        except ImportError:
            pass
        else:
            shutdown_telemetry()


if __name__ == "__main__":
    main()
