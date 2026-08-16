"""Locate the native runtime owned by the libasslite bundle wheel."""

from __future__ import annotations

import json
import os
import sys
import threading
from importlib.resources import files
from pathlib import Path

if sys.platform == "win32":
    from os import add_dll_directory as _add_dll_directory
else:

    def _add_dll_directory(_path: str) -> object:
        raise RuntimeError("DLL directories are only available on Windows")


_DLL_DIRECTORY_LOCK = threading.Lock()
_DLL_DIRECTORY_HANDLES: dict[str, object] = {}
_WINDOWS = sys.platform == "win32"


def _register_dependency_directory(directory: Path) -> None:
    if not _WINDOWS:
        return
    key = os.fspath(directory)
    with _DLL_DIRECTORY_LOCK:
        if key not in _DLL_DIRECTORY_HANDLES:
            _DLL_DIRECTORY_HANDLES[key] = _add_dll_directory(key)


def library_path() -> Path:
    root = files(__package__)
    manifest = json.loads(root.joinpath("native-manifest.json").read_text(encoding="utf-8"))
    relative = manifest.get("library")
    if not isinstance(relative, str) or not relative:
        raise RuntimeError("libasslite bundle manifest has no library")
    library = root.joinpath(relative)
    if not library.is_file():
        raise RuntimeError(f"libasslite bundle payload is missing {relative}")
    path = Path(str(library))
    _register_dependency_directory(path.parent)
    return path


__all__ = ["library_path"]
