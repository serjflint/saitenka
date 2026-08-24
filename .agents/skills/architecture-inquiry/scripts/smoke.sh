#!/usr/bin/env bash
# Smoke: the inquiry contract and every handoff it names must still exist.
# test -f/-e only — grep-free, safe under the search-shim mock.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/references/inquiry-contract.md"
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING: $1"; fail=1; }; }
have README.md
have ARCHITECTURE.md
have .agents/architecture-review/SPEC.md
have .agents/architecture-review/census.json
have .agents/skills/architecture-review/SKILL.md
have .agents/skills/research/SKILL.md
have .agents/skills/plan-migration/SKILL.md
have .agents/skills/contribute/SKILL.md
have .agents/rules/searching.md
have tools/skill_check.py
if [ "$fail" -eq 0 ]; then echo "architecture-inquiry smoke OK"; else echo "architecture-inquiry smoke FAILED"; exit 1; fi
