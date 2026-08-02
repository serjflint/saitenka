#!/usr/bin/env bash
# Smoke: the canonical servers.json renders to valid Claude + Codex MCP config, round-tripping the
# offline guardrails and the write_note approval hint. Behaviour-asserting, no agent prose.
# Pure-Python assertions (no grep/find) — this machine PATH-shims those to a fan-out mock.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_root="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"
render="$skill_dir/scripts/render.py"

test -f "$skill_dir/agents/openai.yaml" || { echo "MISSING openai.yaml"; exit 1; }

claude_out="$(mktemp)"
uv run --no-project --quiet "$render" --agent claude --out "$claude_out" >/dev/null
codex_out="$(uv run --no-project --quiet "$render" --agent codex)"

REPO_ROOT="$repo_root" SKILL_DIR="$skill_dir" CLAUDE_OUT="$claude_out" CODEX_OUT="$codex_out" \
python3 <<'PY'
import json, os, sys

repo, skill = os.environ["REPO_ROOT"], os.environ["SKILL_DIR"]
servers = json.load(open(f"{repo}/.agents/mcp/servers.json"))["servers"]
assert {"repowise", "basic-memory"} <= set(servers), "servers.json: both servers"

claude = json.load(open(os.environ["CLAUDE_OUT"]))["mcpServers"]
assert {"repowise", "basic-memory"} <= set(claude), "claude: both servers"
env = claude["basic-memory"].get("env", {})
assert env.get("BASIC_MEMORY_FORCE_LOCAL") == "true", "claude: FORCE_LOCAL guardrail"
assert env.get("HF_HUB_OFFLINE") == "1", "claude: HF offline guardrail"

codex = os.environ["CODEX_OUT"]
for needle in ("BEGIN saitenka managed MCP", "[mcp_servers.repowise]",
               "[mcp_servers.basic-memory]", 'approval_mode = "approve"'):
    assert needle in codex, f"codex render missing: {needle}"

skill_md = open(f"{skill}/SKILL.md").read().lower()
for agent in ("claude", "codex"):  # render.py's supported targets must be documented
    assert agent in skill_md, f"SKILL.md missing agent column: {agent}"

print("agent-setup smoke OK")
PY
rm -f "$claude_out"
