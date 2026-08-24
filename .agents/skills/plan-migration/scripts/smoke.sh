#!/usr/bin/env bash
# Smoke: every path the skill cites must still exist, or its recipe sends the reader nowhere.
# test -f/-e only — grep-free, safe under the search-shim mock (see AGENTS.md "Tooling").
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/references/leverage-devices.md"
test -f "$skill_dir/references/codemod-recipe.md"
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING: $1"; fail=1; }; }
have tools/cluster_map.py          # the census the design step reads
have tools/host_mass.py            # the standing retirement meter
have tools/host_arity.py           # the debt meter beside it
have tools/codemods/harness.py     # the worklist -> LibCST handoff
have tools/codemods/move_member.py # the runnable worked example
have pyproject.toml                # the poe tasks and the `codemod` dependency group
have .agents/skills/architecture-inquiry/SKILL.md # reverse handoff while architecture is undecided
if [ "$fail" -eq 0 ]; then echo "plan-migration smoke OK"; else echo "plan-migration smoke FAILED"; exit 1; fi
