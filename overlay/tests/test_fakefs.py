"""Filesystem-interaction tests on an in-memory FS (pyfakefs). The `fs` fixture also emulates WINDOWS
semantics on macOS/Linux — so we can exercise case-insensitivity, reserved names, and atomic-write
crash-safety without a real Windows box (and without touching real disk)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pyfakefs.fake_filesystem import OSType

from overlay.app import paths


def test_atomic_write_is_crash_safe(fs, monkeypatch):
    """A failure mid-write (simulated) must leave the ORIGINAL intact and drop no temp litter — the
    whole point of temp + os.replace over a plain write_text."""
    import os

    target = Path("/data/overlay.toml")
    fs.create_file(str(target), contents="original\n")

    def _boom(*a, **k):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        paths.atomic_write_text(target, "new content that must not land\n")

    assert target.read_text() == "original\n"  # untouched
    assert list(Path("/data").glob(".*.tmp")) == []  # temp cleaned up on failure


def test_atomic_write_round_trips_in_memory(fs):
    p = Path("/x/y/z.txt")
    paths.atomic_write_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"


@pytest.mark.windows_sim
def test_windows_fs_is_case_insensitive(fs):
    """Emulated Windows FS on this host: two dict paths differing only in case COLLIDE — the class of
    bug we'd otherwise only see on a Windows machine."""
    fs.os = OSType.WINDOWS
    fs.create_file(r"C:\dicts\Dict.zip", contents="x")
    assert Path(r"C:\dicts\dict.zip").exists()  # case-insensitive lookup hits the same file
