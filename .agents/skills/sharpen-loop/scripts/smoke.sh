#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_dir="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"

test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -f "$repo_dir/.agents/sharpen/SPEC.md"
test -f "$repo_dir/.agents/sharpen/ADAPTERS.md"
test -f "$repo_dir/.agents/sharpen/PROMPTS.md"
test -f "$repo_dir/.agents/sharpen/contracts.json"

if rg -n 'TODO|\[TODO' "$skill_dir/SKILL.md" "$skill_dir/agents" "$skill_dir/references"; then
  echo "sharpen-loop skill contains an unfinished placeholder" >&2
  exit 1
fi

rg -q 'fork_turns="none"' "$skill_dir/SKILL.md"
rg -q '"version": 2' "$repo_dir/.agents/sharpen/contracts.json"
rg -q 'CONTRACT_VERSION = 2' "$repo_dir/.agents/sharpen/harness.js"
rg -q 'better_fix' "$repo_dir/.agents/sharpen/contracts.json"
rg -q 'Better fix hand-off' "$repo_dir/.agents/sharpen/harness.js"
rg -q 'skeptic_verdict' "$repo_dir/.agents/sharpen/harness.js"
rg -q 'judge_verdict' "$repo_dir/.agents/sharpen/harness.js"
rg -Fq "verdict = judge?.verdict === 'UPHELD' ? 'UPHELD' : 'REFUTED'" "$repo_dir/.agents/sharpen/harness.js"
if rg -q "model:" "$repo_dir/.agents/sharpen/harness.js"; then
  echo "Claude adapter hard-codes a provider model" >&2
  exit 1
fi

node "$repo_dir/.agents/sharpen/test_harness.mjs"

echo "sharpen-loop skill smoke: ok"
