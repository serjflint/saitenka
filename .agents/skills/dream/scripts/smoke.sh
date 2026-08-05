#!/usr/bin/env bash
# Smoke: the skill's own files + the repo-side surfaces it names must still exist, or it has rotted.
# dream operates on ~/.claude memory (outside the repo), so only repo-side references are checkable.
# test -f/-e only — grep-free, safe under the search-shim mock (see AGENTS.md "Tooling").
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -f "$skill_dir/references/backup-and-classify.md"   # the packaged taxonomy + backup spec
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING surface: $1"; fail=1; }; }
have AGENTS.md        # the canonical, committed layer dream reads read-only
have .agents/skills
if [ "$fail" -eq 0 ]; then echo "dream smoke OK"; else echo "dream smoke FAILED"; exit 1; fi
