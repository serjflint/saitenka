#!/usr/bin/env python3
"""Session-start hook: keep the repowise index fresh after a merge, agent-agnostically.

repowise's generated wiki/graph (`.repowise/`) is indexed at a specific commit
(`state.json:last_sync_commit`); once HEAD moves past it (a merge, a pull), its answers
go stale. This hook — wired identically into Claude Code and Codex — notices that drift at
the start of a session and either reminds or (opt-in) launches a background refresh.

Behaviour (safe by default):
- No `.repowise/` or `state.json` → silent no-op (repowise isn't set up here).
- Index commit == HEAD → silent no-op (fresh).
- Already acted on this HEAD (sentinel) → silent no-op (no per-tool-call spam if wired to a
  frequent event).
- Stale, and `SAITENKA_REPOWISE_AUTOUPDATE` is truthy AND the MLX doc-gen server is reachable
  → launch `uv run poe repowise-doc-update` DETACHED (the poe task is the SSOT for the flags /
  OPENAI_BASE_URL), logging to `.repowise/refresh.log`. Never blocks the session.
- Stale otherwise → a one-line reminder with the exact refresh command.

The command is NOT hardcoded here — it shells the poe task, so how-to-update stays single-sourced
in `[tool.poe.tasks]`. Wiring lives in each agent's local config (`.claude/settings.json`
SessionStart / Codex hooks); this script is committed so it is reproducible. stdlib-only; reads the
hook JSON on stdin but does not require it; exit 0 always (a crash must not block work).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

_MLX_PORT_DEFAULT = 8080  # matches `poe repowise-mlx-serve` / repowise-doc-update's OPENAI_BASE_URL
_SENTINEL = ".saitenka-refresh-seen"


def _repowise_dir(start: Path) -> Path | None:
    for d in (start, *start.parents):
        if (d / ".repowise").is_dir():
            return d / ".repowise"
    return None


def _short(c: str) -> str:
    return c[:7]


def plan_refresh(
    *, repowise_dir: Path, head: str | None, autoupdate: bool, mlx_up: bool, seen: str | None
) -> tuple[str, str]:
    """Pure decision: ('noop'|'remind'|'update', message). The unit under test."""
    state = repowise_dir / "state.json"
    if not state.is_file():
        return ("noop", "")
    try:
        indexed = json.loads(state.read_text()).get("last_sync_commit")
    except (OSError, ValueError):
        return ("noop", "")
    if not head or not indexed or head in {indexed, seen}:
        return ("noop", "")
    where = f"(indexed {_short(indexed)}, HEAD {_short(head)})"
    if autoupdate and mlx_up:
        return (
            "update",
            f"repowise index stale {where} → launching background `poe repowise-doc-update`",
        )
    return (
        "remind",
        (
            f"repowise index behind HEAD {where} — `poe repowise-doc-update` to refresh "
            "(needs `poe repowise-mlx-serve`; set SAITENKA_REPOWISE_AUTOUPDATE=1 to auto-launch)."
        ),
    )


def _head(repo_root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return r.stdout.strip() or None


def _mlx_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _launch_update(repo_root: Path, repowise_dir: Path) -> None:
    log = (repowise_dir / "refresh.log").open("a")
    subprocess.Popen(
        ["uv", "run", "poe", "repowise-doc-update"],
        cwd=repo_root / "overlay",
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> None:
    try:
        json.load(sys.stdin)  # consume the hook payload if present; we don't need its fields
    except Exception:
        pass
    start = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    repowise_dir = _repowise_dir(start)
    if repowise_dir is None:
        return
    repo_root = repowise_dir.parent
    head = _head(repo_root)
    port = int(os.environ.get("SAITENKA_REPOWISE_MLX_PORT") or _MLX_PORT_DEFAULT)
    autoupdate = (os.environ.get("SAITENKA_REPOWISE_AUTOUPDATE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    sentinel = repowise_dir / _SENTINEL
    seen = sentinel.read_text().strip() if sentinel.is_file() else None

    action, msg = plan_refresh(
        repowise_dir=repowise_dir,
        head=head,
        autoupdate=autoupdate,
        mlx_up=autoupdate and _mlx_reachable(port),  # only probe when we'd actually auto-run
        seen=seen,
    )
    if action == "noop":
        return
    if action == "update":
        try:
            _launch_update(repo_root, repowise_dir)
        except OSError:
            pass
    print(f"[repowise] {msg}", file=sys.stderr)
    if head:
        try:
            sentinel.write_text(head)
        except OSError:
            pass


if __name__ == "__main__":
    main()
