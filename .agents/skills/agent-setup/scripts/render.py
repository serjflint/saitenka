# /// script
# requires-python = ">=3.11"
# dependencies = ["tomli-w>=1.0"]
# ///
"""Render the canonical .agents/mcp/servers.json into a specific agent's MCP config.

There is no shared cross-agent MCP config format, so servers.json is the source of truth and this
script emits each agent's dialect:

  uv run .agents/skills/agent-setup/scripts/render.py --agent claude          # writes ./.mcp.json
  uv run .agents/skills/agent-setup/scripts/render.py --agent codex           # prints a TOML block
  uv run .agents/skills/agent-setup/scripts/render.py --agent codex --out ~/.codex/config.toml

Claude output is a full `.mcp.json`. Codex output is a `[mcp_servers.*]` block wrapped in
`# BEGIN/END saitenka managed MCP` sentinels; with --out it is merged idempotently into that TOML file
(re-running replaces the block, never duplicates it).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import tomli_w

BEGIN = "# BEGIN saitenka managed MCP"
END = "# END saitenka managed MCP"


def repo_root() -> Path:
    # scripts/ -> agent-setup/ -> skills/ -> .agents/ -> repo root
    return Path(__file__).resolve().parents[4]


def load_servers() -> dict:
    src = repo_root() / ".agents" / "mcp" / "servers.json"
    data = json.loads(src.read_text())
    return data["servers"]


def render_claude(servers: dict) -> str:
    out: dict[str, dict] = {}
    for name, spec in servers.items():
        entry = {"command": spec["command"], "args": spec.get("args", [])}
        if spec.get("env"):
            entry["env"] = spec["env"]
        out[name] = entry
    return json.dumps({"mcpServers": out}, indent=2) + "\n"


def render_codex(servers: dict) -> str:
    out: dict[str, dict] = {}
    for name, spec in servers.items():
        entry: dict = {"command": spec["command"], "args": spec.get("args", [])}
        if spec.get("env"):
            entry["env"] = spec["env"]
        # codex approval hints -> [mcp_servers.<name>.tools.<tool>] approval_mode
        approval = (spec.get("codex") or {}).get("approval") or {}
        if approval:
            entry["tools"] = {tool: {"approval_mode": mode} for tool, mode in approval.items()}
        out[name] = entry
    body = tomli_w.dumps({"mcp_servers": out}).strip()
    return f"{BEGIN}\n{body}\n{END}\n"


def merge_codex(block: str, target: Path) -> None:
    existing = target.read_text() if target.exists() else ""
    if BEGIN in existing and END in existing:
        pre = existing[: existing.index(BEGIN)]
        post = existing[existing.index(END) + len(END) :]
        merged = pre.rstrip("\n") + "\n\n" + block + post.lstrip("\n")
    else:
        merged = (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(merged)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent", required=True, choices=["claude", "codex"])
    ap.add_argument("--out", help="Output path. Default: ./.mcp.json for claude; stdout for codex.")
    args = ap.parse_args(argv)

    servers = load_servers()

    if args.agent == "claude":
        rendered = render_claude(servers)
        out = Path(args.out) if args.out else repo_root() / ".mcp.json"
        out.write_text(rendered)
        print(f"wrote {out}", file=sys.stderr)
    else:
        block = render_codex(servers)
        if args.out:
            target = Path(args.out).expanduser()
            merge_codex(block, target)
            print(f"merged managed MCP block into {target}", file=sys.stderr)
        else:
            sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
