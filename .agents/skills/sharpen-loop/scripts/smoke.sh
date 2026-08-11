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

if git -C "$repo_dir" grep -n -E 'TODO|\[TODO' -- \
  .agents/skills/sharpen-loop/SKILL.md .agents/skills/sharpen-loop/agents; then
  echo "sharpen-loop skill contains an unfinished placeholder" >&2
  exit 1
fi

git -C "$repo_dir" grep -q 'fork_turns="none"' -- .agents/skills/sharpen-loop/SKILL.md
git -C "$repo_dir" grep -q '"version": 3' -- .agents/sharpen/contracts.json
git -C "$repo_dir" grep -q 'CONTRACT_VERSION = 3' -- .agents/sharpen/harness.js
git -C "$repo_dir" grep -q 'better_fix' -- .agents/sharpen/contracts.json
git -C "$repo_dir" grep -q 'Better fix hand-off' -- .agents/sharpen/harness.js
git -C "$repo_dir" grep -q 'skeptic_verdict' -- .agents/sharpen/harness.js
git -C "$repo_dir" grep -q 'judge_verdict' -- .agents/sharpen/harness.js
git -C "$repo_dir" grep -Fq "verdict = judge?.verdict === 'UPHELD' ? 'UPHELD' : 'REFUTED'" -- .agents/sharpen/harness.js
if git -C "$repo_dir" grep -q "model:" -- .agents/sharpen/harness.js; then
  echo "Claude adapter hard-codes a provider model" >&2
  exit 1
fi

node "$repo_dir/.agents/sharpen/test_harness.mjs"

echo "sharpen-loop skill smoke: ok"
