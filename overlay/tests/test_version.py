"""overlay_version() pins the running code: a git suffix on a checkout/branch install, plain on PyPI."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import overlay.version as version_mod


def test_overlay_version_appends_git_revision(monkeypatch):
    monkeypatch.setattr(version_mod, "version", lambda _pkg: "1.1.0")
    monkeypatch.setattr(version_mod, "git_revision", lambda: "abc1234")
    assert version_mod.overlay_version() == "1.1.0+gabc1234"


def test_overlay_version_is_plain_off_a_checkout(monkeypatch):
    monkeypatch.setattr(version_mod, "version", lambda _pkg: "1.1.0")
    monkeypatch.setattr(version_mod, "git_revision", lambda: None)  # PyPI install → no git
    assert version_mod.overlay_version() == "1.1.0"


def _fake_git(rev: str, porcelain: str):
    def run(cmd, **_kw):
        if "rev-parse" in cmd:
            return SimpleNamespace(stdout=rev)
        return SimpleNamespace(stdout=porcelain)  # status --porcelain

    return run


def test_git_revision_marks_a_dirty_worktree(monkeypatch):
    version_mod.git_revision.cache_clear()
    monkeypatch.setattr(version_mod.subprocess, "run", _fake_git("932b72a\n", " M file.py\n"))
    assert version_mod.git_revision() == "932b72a-dirty"


def test_git_revision_clean_is_bare_sha(monkeypatch):
    version_mod.git_revision.cache_clear()
    monkeypatch.setattr(version_mod.subprocess, "run", _fake_git("932b72a\n", ""))
    assert version_mod.git_revision() == "932b72a"


def test_git_revision_none_when_not_a_checkout(monkeypatch):
    version_mod.git_revision.cache_clear()

    def boom(*_a, **_k):
        raise subprocess.SubprocessError("not a git repository")

    monkeypatch.setattr(version_mod.subprocess, "run", boom)
    assert version_mod.git_revision() is None
