"""Locate the native runtime owned by the libasslite bundle wheel."""

from __future__ import annotations

import json
import os
import sys
import threading
from importlib.resources import files
from pathlib import Path

if sys.platform == "win32":
    from ctypes import WinDLL
    from os import add_dll_directory as _add_dll_directory

    _load_dll = WinDLL
else:

    def _add_dll_directory(_path: str) -> object:
        raise RuntimeError("DLL directories are only available on Windows")

    def _load_dll(_path: str) -> object:
        raise RuntimeError("DLL loading is only available on Windows")


_DLL_DIRECTORY_LOCK = threading.Lock()
_DLL_DIRECTORY_HANDLES: dict[str, object] = {}
_WINDOWS = sys.platform == "win32"


def _activate_windows_closure(directory: Path, filenames: tuple[str, ...], primary: str) -> None:
    if not _WINDOWS:
        return
    key = os.fspath(directory)
    with _DLL_DIRECTORY_LOCK:
        if key in _DLL_DIRECTORY_HANDLES:
            return
        handles = [_add_dll_directory(key)]
        pending = [directory / name for name in filenames if name.casefold().endswith(".dll")]
        primary_path = directory / primary
        pending = [path for path in pending if path != primary_path]
        while pending:
            deferred: list[Path] = []
            for path in pending:
                try:
                    handles.append(_load_dll(os.fspath(path)))
                except OSError:
                    deferred.append(path)
            if len(deferred) == len(pending):
                break
            pending = deferred
        handles.append(_load_dll(os.fspath(primary_path)))
        _DLL_DIRECTORY_HANDLES[key] = handles


def library_path() -> Path:
    root = files(__package__)
    manifest = json.loads(root.joinpath("native-manifest.json").read_text(encoding="utf-8"))
    relative = manifest.get("library")
    if not isinstance(relative, str) or not relative:
        raise RuntimeError("libasslite bundle manifest has no library")
    filenames = manifest.get("files")
    if not isinstance(filenames, list) or not all(isinstance(name, str) for name in filenames):
        raise RuntimeError("libasslite bundle manifest has no native file inventory")
    library = root.joinpath(relative)
    if not library.is_file():
        raise RuntimeError(f"libasslite bundle payload is missing {relative}")
    path = Path(str(library))
    _activate_windows_closure(path.parent, tuple(filenames), path.name)
    return path


__all__ = ["library_path"]
