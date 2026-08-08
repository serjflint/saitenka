#!/usr/bin/env python3
"""PreToolUse guard: deny Bash commands that invoke the PATH-shimmed search
binaries (grep/find/rg/pgrep/ag), which fork-bomb the find-utils mock in this
environment. Routes the agent to the Grep/Glob tools, the LSP tool, or `git grep`.

Wired from `.claude/settings.json` (local, git-ignored); this logic lives in
`.agents/` so it is committed and reproducible. Pure-Python on purpose — it must
not itself call any shimmed binary. Reads the PreToolUse JSON on stdin; emits a
deny decision as JSON on stdout. Exit 0 always (a crash must not block work).
"""

from __future__ import annotations

import json
import re
import sys

FORBIDDEN = {"grep", "egrep", "fgrep", "find", "rg", "ripgrep", "pgrep", "ag"}
# wrappers that take the real command as an argument (peek past them)
WRAPPERS = {"xargs", "parallel", "time", "nice", "nohup", "sudo", "command", "env", "stdbuf"}


def _base(tok: str) -> str:
    return tok.rsplit("/", 1)[-1].strip("'\"`")


def _strip_noise(cmd: str) -> str:
    """Remove heredoc bodies and quoted strings — their contents are *data*, not
    commands, so a `grep` mentioned in a heredoc or a `"..."` argument (e.g. this
    very PR body, or `echo "use grep"`) must not trip the guard. What remains is
    the executable skeleton, where a real invocation sits at a command position.
    """
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2", " ", cmd, flags=re.DOTALL)  # heredocs
    cmd = re.sub(r"'[^']*'", " ", cmd)  # single-quoted
    return re.sub(r'"[^"]*"', " ", cmd)  # double-quoted


def _offending_binary(cmd: str) -> str | None:
    cmd = _strip_noise(cmd)
    # split into command segments on separators + substitutions
    for seg in re.split(r"\$\(|`|&&|\|\||[|;\n&()]", cmd):
        s = seg.strip()
        # drop leading env-var assignments: FOO=bar BAZ=qux cmd ...
        while re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s):
            parts = s.split(None, 1)
            s = (parts[1] if len(parts) > 1 else "").strip()
        if not s:
            continue
        toks = s.split()
        head = _base(toks[0])
        if head in FORBIDDEN:  # `git grep` is safe: its head is `git`, not `grep`
            return head
        if head in WRAPPERS:
            for t in toks[1:]:
                if _base(t) in FORBIDDEN:
                    return _base(t)
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    hit = _offending_binary(cmd)
    if hit:
        reason = (
            f"`{hit}` fork-bombs the find-utils mock in this repo. "
            "Use the Grep/Glob tools for text/file search, the LSP tool "
            "(findReferences/documentSymbol) for symbol nav, or `git grep` / `git ls-files`."
        )
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


if __name__ == "__main__":
    main()
