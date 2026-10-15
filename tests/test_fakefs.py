"""Atomic-write crash safety and round-trip behavior on an in-memory filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from saitenka.app import paths


def test_atomic_write_is_crash_safe(fs, monkeypatch):
    """A failure mid-write (simulated) must leave the ORIGINAL intact and drop no temp litter — the
    whole point of temp + os.replace over a plain write_text."""
    import os

    target = Path("/data/overlay.toml")
    fs.create_file(str(target), contents="original\n")

    def _boom(*_a, **_k):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        paths.atomic_write_text(target, "new content that must not land\n")

    assert target.read_text(encoding="utf-8") == "original\n"  # untouched
    assert list(Path("/data").glob(".*.tmp")) == []  # temp cleaned up on failure


@pytest.mark.usefixtures("fs")
def test_atomic_write_round_trips_in_memory():
    p = Path("/x/y/z.txt")
    paths.atomic_write_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"
