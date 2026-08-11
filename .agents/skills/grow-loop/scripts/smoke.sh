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

echo "grow-loop skill smoke: ok"
