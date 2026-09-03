#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_dir="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"

test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -f "$repo_dir/.agents/grow/SPEC.md"
test -f "$repo_dir/.agents/grow/ADAPTERS.md"
test -f "$repo_dir/.agents/grow/PROMPTS.md"
test -f "$repo_dir/.agents/grow/contracts.json"
test -f "$repo_dir/.agents/skills/write-test/SKILL.md"
test -f "$repo_dir/.agents/skills/test-adequacy/SKILL.md"
test -f "$repo_dir/.agents/skills/contribute/SKILL.md"
test -f "$repo_dir/.agents/skills/assurance-pipeline/SKILL.md"

uv run python - "$repo_dir/.agents/grow/contracts.json" "$skill_dir/SKILL.md" <<'PY'
import json, sys
contract = json.load(open(sys.argv[1], encoding="utf-8"))
skill = open(sys.argv[2], encoding="utf-8").read()
phase = contract["lifecycle"]["terminal_phase"]
marker = f"Mandatory terminal phase — {phase}."
assert marker in skill, f"grow-loop skill missing terminal phase marker: {marker}"
PY

echo "grow-loop skill smoke: ok"
