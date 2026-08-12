"""Enable/disable/report the telemetry opt-in without hand-editing ``overlay.toml``.

Two independent switches, deliberately kept distinct (the CLI reports both): the config flag
(``[telemetry] enabled`` — "I want telemetry") and the ``telemetry`` extra (the OTel SDK dependency
that makes it actually run). Flipping the config here does NOT install the extra — that's a separate
``uv`` step this can only detect and point at, not perform (the running tool can't reliably resync
its own install env across every packaging shape). Writing the config reuses
:func:`saitenka.app.init_wizard.write_config` so comments/formatting survive and the prior file is
backed up; a no-op change writes nothing (no needless backup)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: The exact command that installs the runtime dependency the config flag needs.
INSTALL_HINT = "uv tool install --reinstall 'saitenka[telemetry]'"


def telemetry_installed() -> bool:
    """True if the OTel SDK (the ``telemetry`` extra) is importable — a real import, the same
    check :func:`saitenka.app.telemetry.configure` makes before standing up providers (``__import__``
    so there's no bound name to trip unused-import lint)."""
    try:
        __import__("opentelemetry.sdk.trace")
    except ImportError:
        return False
    return True


def _config_enabled(cfg: dict) -> bool:
    raw = cfg.get("telemetry")
    return bool(raw.get("enabled", False)) if isinstance(raw, dict) else False


def set_enabled(
    *,
    enabled: bool,
    confirm: Callable[[str], bool] = lambda _p: True,
    dest: str | os.PathLike | None = None,
) -> tuple[bool, Path | None]:
    """Set ``[telemetry] enabled`` to *enabled*. Returns ``(changed, backup_path)``: ``changed`` is
    False (and nothing is written) when the config already holds that value — idempotent, no rewrite,
    no backup. ``backup_path`` is the timestamped backup of a pre-existing file, or ``None``."""
    from pathlib import Path

    from saitenka.app.config import load_config
    from saitenka.app.init_wizard import write_config

    if _config_enabled(load_config(dest)) == enabled:
        return (False, None)
    dest_path = Path(dest) if dest is not None else None
    backup = write_config({"telemetry": {"enabled": enabled}}, confirm=confirm, dest=dest_path)
    return (True, backup)


@dataclass(frozen=True)
class TelemetryState:
    """A point-in-time read of every switch, for the ``telemetry status`` command."""

    config_enabled: bool  # [telemetry] enabled in the config file
    extra_installed: bool  # the telemetry extra (OTel SDK) is importable
    kill_switch: bool  # OTEL_SDK_DISABLED env var is active
    effective: bool  # actually recording: all three of the above align
    export_dir: Path
    trace_path: Path
    trace_exists: bool


def telemetry_state() -> TelemetryState:
    """Compose the config flag, the installed-extra check, and the ``OTEL_SDK_DISABLED`` kill switch
    into whether telemetry is *effectively* recording, plus where its trace lands."""
    from saitenka.app.config import load_config, resolve_telemetry
    from saitenka.app.telemetry import export_dir, latest_trace

    cfg = load_config()
    config_enabled = _config_enabled(cfg)
    kill = os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"true", "1"}
    installed = telemetry_installed()
    exp = export_dir(resolve_telemetry(cfg))
    latest = latest_trace(exp)  # traces rotate (timestamped per session) — report the newest
    return TelemetryState(
        config_enabled=config_enabled,
        extra_installed=installed,
        kill_switch=kill,
        effective=config_enabled and installed and not kill,
        export_dir=exp,
        trace_path=latest or exp,  # the newest trace file, or the dir itself when none exist yet
        trace_exists=latest is not None,
    )
