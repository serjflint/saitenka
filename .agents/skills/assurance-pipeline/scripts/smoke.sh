#!/usr/bin/env bash
# Smoke: the orchestration contract and every routed skill must still exist.
# test -f/-e only -- grep-free, safe under the search-shim mock.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/references/contract.md"
test -f "$skill_dir/references/evidence-ledger.md"
test -f "$skill_dir/references/completion.example.json"
test -f "$skill_dir/scripts/verify_receipt.py"
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING: $1"; fail=1; }; }
have AGENTS.md
have CONTRIBUTING.md
have .agents/skills/architecture-inquiry/SKILL.md
have .agents/skills/grow-loop/SKILL.md
have .agents/skills/sharpen-loop/SKILL.md
have .agents/skills/test-adequacy/SKILL.md
have .agents/skills/write-test/SKILL.md
have .agents/skills/research/SKILL.md
have .agents/skills/contribute/SKILL.md
have .agents/skills/dev-gate/SKILL.md
have .github/PULL_REQUEST_TEMPLATE.md
have .agents/rules/searching.md
have tools/skill_check.py
uv run python "$skill_dir/scripts/verify_receipt.py" --structure-only "$skill_dir/references/completion.example.json"
if [ "$fail" -eq 0 ]; then echo "assurance-pipeline smoke OK"; else echo "assurance-pipeline smoke FAILED"; exit 1; fi
