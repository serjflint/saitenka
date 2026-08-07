#!/usr/bin/env python3
"""PreToolUse guard: deny Bash commands that invoke a BARE ``pip`` / ``python`` /
``pipx`` / ``virtualenv``. This repo is uv-only (AGENTS.md "Python: always uv") — a
bare interpreter uses the wrong env and skips the lockfile. Routes to ``uv run`` /
``uvx`` / ``uv add``.

Why a hook and not just the prose rule: a "never bare pip" restraint rule gets ~0%
compliance as text and never self-corrects (RepoComplianceBench, 2607.26819) — a rule
that *undoes* work has to be a mechanical control, enforced at the moment of action.

Deliberately narrower than the search guard: only a **bare** (no-slash) interpreter is
the footgun. A path-qualified one is a chosen, specific env and is allowed —
``.venv/bin/python``, ``/usr/bin/python3``, ``sudo /tmp/venv-py-spy/bin/python`` (the
``poe *-pyspy`` tasks print exactly that). ``uv``/``uvx`` heads pass (``uv run python``,
``uv pip install``). ``python`` as a mere *argument* (``which python``, ``brew install
python``) is not an invocation, so only a segment's command position is checked.

Wired from ``.claude/settings.json`` (local, git-ignored); the logic lives in ``.agents/``
so it is committed and reproducible. stdlib-only. Reads the PreToolUse JSON on stdin;
emits a deny decision as JSON on stdout. Exit 0 always (a crash must not block work).
"""

from __future__ import annotations

import json
import re
import sys

# bare (no-slash) exec-target heads that mean "wrong env / no lockfile".
_PIP_FAMILY = {"pip", "pip3", "pipx", "virtualenv", "easy_install"}
_PYTHON = re.compile(r"python(\d+(\.\d+)?)?$")  # python, python3, python3.13
_UV = {"uv", "uvx"}  # an uv/uvx head (or exec-target) is the sanctioned form
# wrappers that take the real command as an argument (peek past them)
_WRAPPERS = {"xargs", "parallel", "time", "nice", "nohup", "sudo", "command", "env", "stdbuf"}


def _clean(tok: str) -> str:
    return tok.strip("'\"`")


def _is_bare_forbidden(tok: str) -> bool:
    if "/" in tok:  # path-qualified interpreter is a deliberate, specific env — allowed
        return False
    base = _clean(tok)
    return base in _PIP_FAMILY or bool(_PYTHON.fullmatch(base))


def _strip_noise(cmd: str) -> str:
    """Drop heredoc bodies and quoted strings — their contents are *data*, so a
    ``python`` inside ``echo "run python"`` or a heredoc must not trip the guard."""
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2", " ", cmd, flags=re.S)  # heredocs
    cmd = re.sub(r"'[^']*'", " ", cmd)  # single-quoted
    cmd = re.sub(r'"[^"]*"', " ", cmd)  # double-quoted
    return cmd


def _exec_target(toks: list[str]) -> str | None:
    """The command token a segment actually runs: peel leading wrappers and their
    flags. Returns None if nothing runnable resolves (err toward NOT flagging)."""
    i = 0
    while i < len(toks):
        tok = toks[i]
        if _clean(tok).rsplit("/", 1)[-1] in _WRAPPERS:
            i += 1
            while i < len(toks) and toks[i].startswith("-"):  # skip the wrapper's flags
                i += 1
            continue
        return tok
    return None


def offending(cmd: str) -> str | None:
    """The bare interpreter a command would invoke, or None. Pure — the unit under test."""
    cmd = _strip_noise(cmd)
    for seg in re.split(r"\$\(|`|&&|\|\||[|;\n&()]", cmd):
        s = seg.strip()
        while re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s):  # drop FOO=bar env-assign prefix
            parts = s.split(None, 1)
            s = (parts[1] if len(parts) > 1 else "").strip()
        toks = s.split()
        target = _exec_target(toks) if toks else None
        if target is None:
            continue
        if _clean(target).rsplit("/", 1)[-1] in _UV:  # uv run python / uv pip / uvx …
            continue
        if _is_bare_forbidden(target):
            return _clean(target)
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    hit = offending(cmd)
    if hit:
        reason = (
            f"bare `{hit}` — this repo is uv-only (AGENTS.md 'Python: always uv'): a bare "
            "interpreter uses the wrong env and skips the lockfile. Use `uv run` / `uvx` / "
            "`uv add` (a path-qualified interpreter like `.venv/bin/python` is fine)."
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
