"""Shared helpers for the commit-time PreToolUse hooks (`comment-review.py`, `test-kinds-advisory.py`).

Both fire on `git commit`, inspect the staged diff, and surface a one-time advisory — so the commit
detection and the git shell-out live here once. Extracted verbatim from comment-review.py; `overlay/tests/
test_test_kinds_advisory.py` covers `is_git_commit` (its first test). stdlib only; a hook must never crash
work, so callers keep their own try/except around these.
"""

from __future__ import annotations

import re
import subprocess

_WRAPPERS = {"sudo", "env", "time", "nice", "nohup", "command", "stdbuf"}


def _base(tok: str) -> str:
    return tok.rsplit("/", 1)[-1].strip("'\"`")


def _strip_noise(cmd: str) -> str:
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2", " ", cmd, flags=re.DOTALL)  # heredocs
    cmd = re.sub(r"'[^']*'", " ", cmd)
    return re.sub(r'"[^"]*"', " ", cmd)


def _git_subcommand(toks: list[str]) -> str | None:
    """The git subcommand token, skipping global flags and their values (`-C dir`, `-c k=v`). None if none."""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in {"-C", "-c", "--git-dir", "--work-tree"}:  # these take a value
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return None


def is_git_commit(cmd: str) -> bool:
    """True if the command runs `git commit` at a command position (not inside a string / `git log`)."""
    for seg in re.split(r"\$\(|`|&&|\|\||[|;\n&()]", _strip_noise(cmd)):
        toks = seg.split()
        while toks and (_base(toks[0]) in _WRAPPERS or "=" in toks[0]):
            toks = toks[1:]
        if toks and _base(toks[0]) == "git" and _git_subcommand(toks) == "commit":
            return True
    return False


def run_git(args: list[str]) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return r.stdout
