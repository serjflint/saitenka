#!/usr/bin/env bash
# Smoke: the SSOT files + canonical examples the SKILL.md cites must still exist. test -f/-e only —
# grep-free, safe under the search-shim mock (grep/find fork-bomb; see AGENTS.md "Tooling").
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING: $1"; fail=1; }; }
have tools/mutate/run.py                 # the mutation allowlist SSOT (TARGETS)
have tools/fuzz/fuzz_sub_index.py        # the `poe fuzz` target
have tests/test_sub_index_properties.py  # the survivor -> property + @example example
have tests/test_mutate_targets.py        # the allowlist rot-guard the skill points at
have tests/conftest.py                   # the crosshair backend registration
if [ "$fail" -eq 0 ]; then echo "test-adequacy smoke OK"; else echo "test-adequacy smoke FAILED"; exit 1; fi
