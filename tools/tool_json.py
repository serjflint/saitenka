"""Fail-closed subprocess JSON reader for loop measurement instruments."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class InstrumentError(RuntimeError):
    """A measurement instrument did not produce trustworthy output."""


def repository_root(start: Path) -> Path:
    """Resolve the containing worktree instead of trusting the launch directory."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise InstrumentError("instrument unavailable: git") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = proc.stderr.strip() or "no repository root"
        raise InstrumentError(f"repository resolution failed: {detail}")
    return Path(proc.stdout.strip()).resolve()


def repository_path(root: Path, supplied: Path | None, default: str) -> Path:
    path = supplied or Path(default)
    return path if path.is_absolute() else root / path


def run_json[JsonShape: (list, dict)](
    cmd: list[str], cwd: Path, expected: type[JsonShape]
) -> JsonShape:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise InstrumentError(f"instrument unavailable: {cmd[0]}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "no stderr"
        raise InstrumentError(f"instrument failed ({proc.returncode}): {' '.join(cmd)}: {detail}")
    if not proc.stdout.strip():
        raise InstrumentError(f"instrument returned empty output: {' '.join(cmd)}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise InstrumentError(f"instrument returned malformed JSON: {' '.join(cmd)}") from exc
    if not isinstance(payload, expected):
        raise InstrumentError(
            f"instrument returned {type(payload).__name__}, expected {expected.__name__}: {' '.join(cmd)}"
        )
    return payload
