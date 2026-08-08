#!/usr/bin/env python3
"""PreToolUse guard: force a review of long NEW comments before they are committed.

Comment bloat is a semantic call — a regex can't tell a real pipeline name (`Stage 5 — raster a
band`) from a process scar (`Stage 5, as I did earlier`), which is why the AGENTS.md "Comments"
discipline is a review rule, not a lint. This hook keeps the *verdict* with the agent but makes the
*review* mechanical: on `git commit`, it surfaces every long added `#`-comment block in the staged
diff and blocks once, quoting the rules, so the agent must consciously compress-or-justify each before
the comment becomes permanent. (RepoComplianceBench, 2607.26819: a restraint rule earns ~0% compliance
as prose, but an additive feedback loop that *surfaces* the work recovers to 77-100%.)

Not a ban and not a loop: it blocks a given flagged set exactly ONCE. The agent then either edits +
re-stages (a new/empty set), or — deciding the comment is justified — re-runs the same commit, whose
signature now matches the recorded ack, and it is allowed. A justified long comment is never rejected.

Honest limit: it forces the comments into context and forces the pause; it cannot force genuine
thought (no hook can). Wired from each agent's local config; committed here so it is reproducible.
stdlib-only; reads the PreToolUse JSON on stdin; exit 0 always (a crash must not block work).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# thresholds — a block is "long" (worth a review) if ANY holds; tune via env if noisy.
_MIN_LINES = 5  # consecutive added comment lines
_MAX_BLOCK_CHARS = 300  # total chars across the block
_MAX_LINE_CHARS = 200  # a single dense one-liner

_WRAPPERS = {"sudo", "env", "time", "nice", "nohup", "command", "stdbuf"}


def _base(tok: str) -> str:
    return tok.rsplit("/", 1)[-1].strip("'\"`")


def _strip_noise(cmd: str) -> str:
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2", " ", cmd, flags=re.DOTALL)  # heredocs
    cmd = re.sub(r"'[^']*'", " ", cmd)
    return re.sub(r'"[^"]*"', " ", cmd)


def is_git_commit(cmd: str) -> bool:
    """True if the command runs `git commit` at a command position (not inside a string / `log`)."""
    for seg in re.split(r"\$\(|`|&&|\|\||[|;\n&()]", _strip_noise(cmd)):
        toks = seg.split()
        while toks and (_base(toks[0]) in _WRAPPERS or "=" in toks[0]):
            toks = toks[1:]
        if not toks or _base(toks[0]) != "git":
            continue
        i = 1  # find git's subcommand: first bare token, skipping global flags + their values
        while i < len(toks):
            t = toks[i]
            if t in {"-C", "-c", "--git-dir", "--work-tree"}:  # these take a value
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            if t == "commit":  # this segment is a commit; else keep scanning later segments
                return True
            break
    return False


_ADD_FILE = re.compile(r"^\+\+\+ b/(.*)$")


def flagged_comments(diff: str) -> list[tuple[str, str]]:
    """(path, block-text) for every long added `#`-comment block in `.py` files of a unified diff."""
    out: list[tuple[str, str]] = []
    path: str | None = None
    block: list[str] = []

    def flush() -> None:
        if block and path:
            joined = "\n".join(block)
            if (
                len(block) >= _MIN_LINES
                or len(joined) >= _MAX_BLOCK_CHARS
                or any(len(line) > _MAX_LINE_CHARS for line in block)
            ):
                out.append((path, joined))
        block.clear()

    for line in diff.splitlines():
        m = _ADD_FILE.match(line)
        if m:
            flush()
            path = m.group(1).strip()
            continue
        if line.startswith(("@@", "--- ", "+++ ")):
            flush()
            continue
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:].strip()
            if path and path.endswith(".py") and content.startswith("#"):
                block.append(content)
                continue
        flush()  # any non-(added-comment) line ends the run
    flush()
    return out


def signature(flagged: list[tuple[str, str]]) -> str:
    payload = "\0".join(f"{p}\0{t}" for p, t in flagged)
    return hashlib.sha256(payload.encode()).hexdigest()


def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return r.stdout


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


def _reason(flagged: list[tuple[str, str]]) -> str:
    lines = [
        (
            f"{len(flagged)} long NEW comment block(s) staged — review each against AGENTS.md "
            '"Comments" before committing:'
        ),
        "  • Information delta only — the *why* / a gotcha / a constraint / a ref, never the *what*.",
        "  • Distil to the irreducible signal — one tight clause beats a paragraph; no teaching tone.",
        '  • No process scars — no `(plan R4)`, `Stage N`, "as discussed".',
        "Compress or justify each, then re-run the SAME commit to confirm (or edit + re-stage).",
        "",
    ]
    for path, block in flagged:
        head = block.splitlines()[0]
        preview = head if len(head) <= 90 else head[:87] + "…"
        lines.append(f"  {path}: {preview}")
    return "\n".join(lines)


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
    flagged = flagged_comments(_git(["diff", "--cached"]))
    if not flagged:
        return
    sig = signature(flagged)
    ack = Path(_git(["rev-parse", "--git-dir"]).strip() or ".git") / "comment-review-ack"
    if ack.is_file() and ack.read_text().strip() == sig:
        return  # already reviewed this exact set → let the retry through
    try:
        ack.write_text(sig)
    except OSError:
        pass
    _deny(_reason(flagged))


if __name__ == "__main__":
    main()
