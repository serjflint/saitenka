#!/usr/bin/env python3
"""PreToolUse advisory: at commit time, name the non-unit oracle KINDS the touched subsystems warrant.

The default reflex is a unit test, so every other kind (integration / invariant / assembly / differential
/ stateful …) only lands when a human nudges for it. This automates that nudge: on `git commit`, it maps
the staged SOURCE files to subsystems via the agent-agnostic `test_kinds` map (the active form of
`coverage-matrix.md`) and surfaces the applicable kinds once, so the agent consciously picks a kind instead
of defaulting to unit-only. Same commit-time surface-once-then-ack shape as comment-review.py (its
RepoComplianceBench basis: a restraint rule is ~0% as prose but recovers as an additive feedback loop).

It is a NUDGE, not a gate — whether the right oracle is *adequate* is semantic (the Grow/Sharpen/mutation
loops own that). A thin agent-specific wrapper: all logic is in `.agents/hooks/{test_kinds,_hooklib}.py`
(agnostic, reused by a Codex wrapper too). Fires once per subsystem-set per session; re-running the SAME
commit proceeds. stdlib-only; exit 0 always (a crash must never block a commit).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import test_kinds
from _hooklib import is_git_commit, run_git


def _already_shown(sig: str, session_id: str) -> bool:
    """True (and records it) if this subsystem-set was already surfaced this session — nudge once."""
    slug = re.sub(r"[^A-Za-z0-9_-]", "", session_id) or "nosession"
    ack = Path(run_git(["rev-parse", "--git-dir"]).strip() or ".git") / f"test-kinds-ack-{slug}"
    seen = ack.read_text(encoding="utf-8").splitlines() if ack.is_file() else []
    if sig in seen:
        return True
    try:
        ack.write_text("\n".join([*seen, sig]) + "\n", encoding="utf-8")
    except OSError:
        pass
    return False


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not is_git_commit(cmd):
        return
    paths = run_git(["diff", "--cached", "--name-only"]).splitlines()
    hits = test_kinds.applicable(paths)
    if not hits:
        return  # no kind-bearing subsystem touched → stay quiet (e.g. a docs / test-only commit)
    if _already_shown(test_kinds.signature(hits), data.get("session_id", "")):
        return  # surfaced this subsystem-set already this session → let the commit through
    _deny(test_kinds.advisory_text(hits))


if __name__ == "__main__":
    main()
