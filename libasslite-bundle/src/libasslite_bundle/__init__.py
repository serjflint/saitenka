"""Locate the native runtime owned by the libasslite bundle wheel."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path


def library_path() -> Path:
    root = files(__package__)
    manifest = json.loads(root.joinpath("native-manifest.json").read_text(encoding="utf-8"))
    relative = manifest.get("library")
    if not isinstance(relative, str) or not relative:
        raise RuntimeError("libasslite bundle manifest has no library")
    library = root.joinpath(relative)
    if not library.is_file():
        raise RuntimeError(f"libasslite bundle payload is missing {relative}")
    return Path(str(library))


__all__ = ["library_path"]
