#!/usr/bin/env bash
# Smoke: the skill's own files + the sibling surfaces it points at must still exist, or it has rotted.
# test -f/-e only — grep-free, safe under the search-shim mock (see AGENTS.md "Tooling").
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -f "$skill_dir/references/authoring-skills.md"   # the packaged, self-contained guide
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING surface: $1"; fail=1; }; }
have AGENTS.md
have .agents/skills
have .agents/skills/agents-md      # the routing skill this one defers "where does it belong?" to
have .agents/skills/write-test     # an exemplar skill to model structure on
have tools/skill_check.py          # the repo-wide discovery/frontmatter contract
if [ "$fail" -eq 0 ]; then echo "skill-creator smoke OK"; else echo "skill-creator smoke FAILED"; exit 1; fi
