"""Non-destructive reinstall + destructive uninstall of saitenka's OWN footprint.

Two operations the CLI wraps:

* **reinstall** — ``uv tool install --reinstall`` *replaces* the extras set, so a bare
  ``saitenka-overlay`` silently drops ``[full]``/``[deinflect]`` (the friend lost the deinflect add-on
  this way). We detect the currently-installed extras from importable marker packages and reinstall
  WITH them — non-destructive.
* **uninstall** — remove saitenka's config / dict DB / cache (logs, telemetry) / crash reports and the
  mpv plugin. NEVER touches mpv or ffmpeg: those are shared tools the user installed themselves, so a
  full uninstall of saitenka must leave them working.
"""

from __future__ import annotations

import importlib.util
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    Confirm = Callable[[str], bool]

# extra -> a package that is importable ONLY when that extra was installed. Lets a --reinstall preserve
# what's actually present without recording the original install spec anywhere.
_EXTRA_PROBES = {
    "deinflect": "saitenka_deinflect",
    "telemetry": "opentelemetry",
    "jmdict": "jamdict",
}

# The overlay lives in the repo's overlay/ subdirectory; uv maps its `../deinflect` path source to the
# repo's deinflect/ subdirectory, so a git install carries the GPL add-on too (verified via dry-run).
_GIT_URL = "git+https://github.com/serjflint/saitenka.git"


def detect_extras() -> list[str]:
    """The extras currently installed, inferred from their importable marker packages (sorted)."""
    return sorted(
        x for x, mod in _EXTRA_PROBES.items() if importlib.util.find_spec(mod) is not None
    )


def reinstall_command(
    extras: list[str], *, source: str = "pypi", ref: str | None = None
) -> list[str]:
    """A ``uv tool install --reinstall`` command that keeps the given extras. ``source``:
    ``"pypi"`` → ``saitenka-overlay[extras]`` (once published); ``"github"`` → the same package pulled
    from GitHub (latest on the default branch, or a ``ref`` tag/branch), which always works and carries
    the GPL ``deinflect`` add-on from the repo's ``deinflect/`` subdirectory. Pure — unit-tested."""
    es = f"[{','.join(sorted(extras))}]" if extras else ""
    if source == "github":
        spec = f"saitenka-overlay{es} @ {_GIT_URL}{f'@{ref}' if ref else ''}#subdirectory=overlay"
    else:
        spec = f"saitenka-overlay{es}"
    return ["uv", "tool", "install", "--reinstall", spec]


def latest_release_tag(timeout: float = 5.0) -> str | None:
    """The newest GitHub release tag (e.g. ``v0.5.0``), or ``None`` when offline / no releases — so
    reinstall can default to the latest RELEASE rather than bleeding-edge ``main``. The release itself
    ships a packaged zip (wheel + installer); installing this tag from git builds the same source."""
    import json
    import urllib.request

    url = "https://api.github.com/repos/serjflint/saitenka/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            tag = json.loads(r.read()).get("tag_name")
    except (OSError, ValueError):
        return None
    return tag if isinstance(tag, str) and tag else None


def reinstall_attempts(
    extras: list[str], *, source: str = "auto", github_ref: str | None = None
) -> list[list[str]]:
    """The reinstall command(s) to try, in order, for the chosen ``source``: ``"pypi"`` / ``"github"``
    force one; ``"auto"`` (default) tries PyPI then falls back to GitHub. ``github_ref`` is the tag/branch
    the GitHub attempt targets (a resolved release tag, an explicit pin, or ``None`` = default branch) —
    it selects WHICH ref, not the source. Pure — unit-tested."""
    if source == "pypi":
        return [reinstall_command(extras, source="pypi")]
    github = reinstall_command(extras, source="github", ref=github_ref)
    if source == "github":
        return [github]
    return [reinstall_command(extras, source="pypi"), github]


def uninstall_targets(*, keep_dicts: bool = False) -> list[Path]:
    """saitenka's OWN dirs to delete — config, data (dict DB), cache (logs/telemetry), crash reports —
    de-duplicated by resolved path, existing only. ``keep_dicts`` preserves the data dir (the expensive
    dictionary DB) even when it coincides with another dir. mpv/ffmpeg live nowhere in here."""
    from overlay.app.crashlog import crash_dir
    from overlay.app.paths import cache_dir, config_dir, data_dir

    keep = {data_dir().resolve()} if keep_dicts else set()
    out: list[Path] = []
    seen: set[Path] = set()
    for d in (config_dir(), data_dir(), cache_dir(), crash_dir()):
        rp = d.resolve()
        if rp in keep or rp in seen or not d.exists():
            continue
        seen.add(rp)
        out.append(d)
    return out


def uninstall(
    confirm: Confirm, *, keep_dicts: bool = False, remove_plugin: bool = True
) -> list[Path]:
    """Delete saitenka's data (confirm-gated) and remove the mpv plugin; return what was deleted. Leaves
    mpv/ffmpeg installed. The plugin removal only touches OUR ``saitenka.lua``, never mpv itself."""
    if remove_plugin:
        from overlay.app.plugin import uninstall_plugin

        try:
            uninstall_plugin()  # removes only saitenka.lua (backs it up) — mpv keeps working
        except OSError:
            pass
    targets = uninstall_targets(keep_dicts=keep_dicts)
    if not targets:
        return []
    if not confirm(f"Delete saitenka's data ({len(targets)} dir(s))? mpv/ffmpeg stay installed"):
        return []
    for d in targets:
        shutil.rmtree(d, ignore_errors=True)
    return targets
