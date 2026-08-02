#!/usr/bin/env bash
# Smoke: the canonical servers.json and the sibling setup skills the doc points at still exist and name
# the tools this SKILL.md routes to. Pure-Python assertions (no grep/find — PATH-shimmed here).
# PATH checks are advisory: a contributor without the optional stack should not fail the rot-guard.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_root="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"

REPO_ROOT="$repo_root" SKILL_DIR="$skill_dir" python3 <<'PY'
import json, os

repo, skill = os.environ["REPO_ROOT"], os.environ["SKILL_DIR"]

servers = json.load(open(f"{repo}/.agents/mcp/servers.json"))["servers"]
assert {"repowise", "basic-memory"} <= set(servers), "servers.json: both servers"
assert servers["basic-memory"]["env"]["HF_HUB_OFFLINE"] == "1", "basic-memory offline guardrail"

# the skills this one hands off to must exist
for path in (".agents/skills/agent-setup/SKILL.md", ".agents/skills/pyrefly-lsp/.lsp.json"):
    assert os.path.exists(f"{repo}/{path}"), f"missing sibling: {path}"

# SKILL.md routes to the three tools by name
md = open(f"{skill}/SKILL.md").read().lower()
for tool in ("repowise", "lsp", "basic memory"):
    assert tool in md, f"SKILL.md no longer mentions: {tool}"

print("agent-tooling smoke OK (structure)")
PY

# Advisory: note (do not fail) when the optional binaries aren't installed.
for bin in repowise pyrefly basic-memory; do
  command -v "$bin" >/dev/null 2>&1 || echo "note: '$bin' not on PATH (optional stack not installed)"
done
