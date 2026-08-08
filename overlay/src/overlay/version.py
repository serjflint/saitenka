"""The installed overlay version string — a core leaf helper (no app/render deps).

Lives at the package root, not in ``app.report``, so ``app.doctor`` (and anyone else) can read the
version without importing ``report`` — which would close a ``report ↔ doctor`` package cycle
(``report`` bundles ``doctor``'s checks; ``doctor`` only ever needed this one string back). See the
``.importlinter`` no-cycles contract.
"""

from __future__ import annotations

import functools
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


@functools.cache
def git_revision() -> str | None:
    """``<short-sha>[-dirty]`` when running from a git checkout (an editable / branch install), else
    ``None`` for a packaged (PyPI) install or when git isn't available. Lets a report/doctor line pin
    the exact code running — a plain ``1.1.0`` can't tell a branch build from the release. Cached: the
    two git calls run once per process, off any hot path (version is read for reports/doctor only)."""
    repo = Path(__file__).resolve().parent
    try:
        rev = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=1.0,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None  # not a checkout, git missing, or slow/hung → no suffix
    if not rev:
        return None
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1.0,
        check=False,
    ).stdout.strip()
    return f"{rev}-dirty" if dirty else rev


def overlay_version() -> str:
    try:
        base = version("saitenka")
    except PackageNotFoundError:  # pragma: no cover — source checkout without an installed dist
        base = "0+unknown"
    rev = git_revision()
    return f"{base}+g{rev}" if rev else base
