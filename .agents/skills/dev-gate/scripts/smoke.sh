#!/usr/bin/env bash
# Smoke: every poe task the SKILL.md documents must exist, and the `all` sequence must match.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/agents/openai.yaml"
cd "$skill_dir/../../.."
PT="overlay/pyproject.toml"
fail=0

# The 14 tasks named in the gate table + advisory tier
for t in lint types arch invariants complexity test test-ft cov audit deps licenses spell \
         links shell hygiene deadcode dup perf-risk complexity-baseline; do
  grep -qE "^$t = |^\[tool\.poe\.tasks\.$t\]" "$PT" || { echo "MISSING poe task: $t"; fail=1; }
done

# The documented `all` sequence must still contain the 14 gate tasks in order-ish (membership check)
allline=$(grep -E "^all = \[" "$PT")
for t in lint types arch invariants complexity test test-ft cov audit deps licenses spell links shell; do
  echo "$allline" | grep -q "\"$t\"" || { echo "poe all no longer includes: $t"; fail=1; }
done

if [ "$fail" -eq 0 ]; then echo "dev-gate smoke OK"; else echo "dev-gate smoke FAILED"; exit 1; fi
