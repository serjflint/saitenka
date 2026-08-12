#!/usr/bin/env bash
# Smoke: the skill's vendored reference + every config surface it routes to must still exist, or the skill
# has rotted. test -f/-e only — grep-free, safe under the search-shim mock (see AGENTS.md "Tooling").
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -f "$skill_dir/references/writing-agents-md.md"   # the packaged, self-contained guide
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING surface: $1"; fail=1; }; }
# Every routing target the skill names must be a real surface in this repo
have AGENTS.md
have .agents/rules
have .agents/skills
have .agents/hooks/block-shell-search.py
have .agents/mcp/servers.json
have pyproject.toml            # the poe gates (required in `all` vs advisory)
if [ "$fail" -eq 0 ]; then echo "agents-md smoke OK"; else echo "agents-md smoke FAILED"; exit 1; fi
